import dataclasses
import math
from typing import Any, Dict, List, Optional, Set, Type, Union

import torch
import torch.distributed
from vllm.distributed import get_kv_transfer_group, get_pp_group
from vllm.logger import init_logger
from vllm.lora.layers import LoRAMapping
from vllm.lora.request import LoRARequest
from vllm.model_executor import SamplingMetadata
from vllm.multimodal import MultiModalKwargs, MultiModalPlaceholderMap
from vllm.platforms import current_platform
from vllm.prompt_adapter.layers import PromptAdapterMapping
from vllm.prompt_adapter.request import PromptAdapterRequest
from vllm.sampling_params import SamplingParams
from vllm.sequence import SequenceGroupMetadata, IntermediateTensors
from vllm.utils import flatten_2d_lists, get_kv_cache_torch_dtype
from vllm.worker.model_runner import (ModelInputForGPU,
                                      ModelInputForGPUBuilder,
                                      ModelInputForGPUWithSamplingMetadata,
                                      ModelRunner)
from vllm.forward_context import set_forward_context
from vllm.model_executor.layers.sampler import SamplerOutput
from vllm.worker.model_runner_base import dump_input_when_exception

logger = init_logger(__name__)

LORA_WARMUP_RANK = 8


class ModelInputForNPUBuilder(ModelInputForGPUBuilder):
    """Build ModelInputForGPU from SequenceGroupMetadata."""

    # Note: ideally we would be using a dataclass(kw_only=True)
    # here, so that this can be subclassed easily,
    # but kw_only is not supported in python<3.10.
    def build(self) -> ModelInputForGPU:
        """Finalize the builder intermediate data and
        create on-device tensors.
        """
        # Combine and flatten intermediate data.
        input_tokens = [
            flatten_2d_lists(inter_data.input_tokens)
            for inter_data in self.inter_data_list
        ]
        if not input_tokens:
            # This may happen when all prefill requests hit
            # prefix caching and there is no decode request.
            return self.model_input_cls()

        mrope_input_positions: Optional[List[List[int]]] = None
        if any(inter_data.mrope_input_positions is not None
               for inter_data in self.inter_data_list):
            mrope_input_positions = [[] for _ in range(3)]
            # calculate max position length for padding
            input_position_lens = [
                len(inter_data.input_positions[0])
                for inter_data in self.inter_data_list
            ]
            max_pos_len = max(input_position_lens)

            for idx in range(3):
                for inter_data in self.inter_data_list:
                    msections = inter_data.mrope_input_positions
                    if msections is None:
                        for _seq_input_positions in inter_data.input_positions:
                            # zero pad
                            _seq_input_positions.extend(
                                [0] *
                                (max_pos_len - len(_seq_input_positions)))
                            mrope_input_positions[idx].extend(
                                _seq_input_positions)
                    else:
                        for _seq_mrope_input_positions in msections:
                            # zero pad
                            _seq_mrope_input_positions[idx].extend(
                                [0] * (max_pos_len -
                                       len(_seq_mrope_input_positions[idx])))
                            mrope_input_positions[idx].extend(
                                _seq_mrope_input_positions[idx])
            input_positions = None
        else:
            input_positions = [
                flatten_2d_lists(inter_data.input_positions)
                for inter_data in self.inter_data_list
            ]

        seq_lens = []
        max_decode_seq_len = 0
        for inter_data in self.inter_data_list:
            seq_lens.extend(inter_data.seq_lens)
            if not inter_data.is_prompt:
                max_decode_seq_len = max(max_decode_seq_len,
                                         max(inter_data.seq_lens))
        query_lens = flatten_2d_lists(
            [inter_data.query_lens for inter_data in self.inter_data_list])
        # Mapping from request IDs to sequence IDs. Used for Jamba models
        # that manages the cache by itself.
        request_ids_to_seq_ids = {
            data.request_id: data.seq_ids
            for data in self.inter_data_list
        }

        input_tokens_tensor = torch.tensor(flatten_2d_lists(input_tokens),
                                           dtype=torch.long,
                                           device=self.runner.device)
        if mrope_input_positions is not None:
            input_positions_tensor = torch.tensor(mrope_input_positions,
                                                  dtype=torch.long,
                                                  device=self.runner.device)
        else:
            input_positions_tensor = torch.tensor(
                flatten_2d_lists(input_positions),
                dtype=torch.long,
                device=self.runner.device)

        # Attention metadata.
        attn_metadata = self.attn_metadata_builder.build(seq_lens, query_lens)

        # LoRA data.
        lora_requests = set()
        lora_mapping = None
        if self.enable_lora:
            lora_requests = set(r for data in self.inter_data_list
                                for r in data.lora_requests)
            lora_index_mapping = flatten_2d_lists([
                flatten_2d_lists(inter_data.lora_index_mapping)
                for inter_data in self.inter_data_list
            ])
            lora_prompt_mapping = flatten_2d_lists([
                flatten_2d_lists(inter_data.lora_prompt_mapping)
                for inter_data in self.inter_data_list
            ])
            lora_mapping = LoRAMapping(
                **dict(index_mapping=lora_index_mapping,
                       prompt_mapping=lora_prompt_mapping,
                       is_prefill=not self.decode_only))

        # Prompt adapter data.
        prompt_adapter_requests: Set[PromptAdapterRequest] = set()
        prompt_adapter_mapping = None
        if self.enable_prompt_adapter:
            prompt_adapter_requests = set(
                data.prompt_adapter_request for data in self.inter_data_list
                if data.prompt_adapter_request is not None)
            prompt_adapter_index_mapping = flatten_2d_lists([
                inter_data.prompt_adapter_index_mapping
                for inter_data in self.inter_data_list
            ])
            prompt_adapter_prompt_mapping = flatten_2d_lists([
                inter_data.prompt_adapter_prompt_mapping
                for inter_data in self.inter_data_list
            ])
            prompt_adapter_mapping = PromptAdapterMapping(
                prompt_adapter_index_mapping,
                prompt_adapter_prompt_mapping,
            )

        # Multi-modal data.
        multi_modal_kwargs_list = [
            data.multi_modal_kwargs for data in self.inter_data_list
            if data.multi_modal_kwargs is not None
        ]
        multi_modal_kwargs = MultiModalKwargs.batch(multi_modal_kwargs_list)

        return self.model_input_cls(
            input_tokens=input_tokens_tensor,
            input_positions=input_positions_tensor,
            attn_metadata=attn_metadata,
            seq_lens=seq_lens,
            query_lens=query_lens,
            lora_mapping=lora_mapping,
            lora_requests=lora_requests,
            multi_modal_kwargs=multi_modal_kwargs,
            request_ids_to_seq_ids=request_ids_to_seq_ids,
            finished_requests_ids=self.finished_requests_ids,
            prompt_adapter_mapping=prompt_adapter_mapping,
            prompt_adapter_requests=prompt_adapter_requests)

    class InterDataForSeqGroup:
        """Intermediate data for the current sequence group."""

        def simple_reinit(self):
            self.input_tokens[0].clear()  # type: ignore
            self.input_positions[0].clear()  # type: ignore
            self.token_types[0].clear()  # type: ignore
            self.mrope_input_positions = None  # type: ignore
            self.seq_lens[0] = 0  # type: ignore
            self.orig_seq_lens[0] = 0  # type: ignore
            self.query_lens[0] = 0  # type: ignore
            self.context_lens[0] = 0  # type: ignore
            self.curr_sliding_window_blocks[0] = 0  # type: ignore
            self.lora_index_mapping.clear()  # type: ignore
            self.lora_prompt_mapping.clear()  # type: ignore
            self.lora_requests.clear()  # type: ignore
            self.prompt_adapter_index_mapping.clear()  # type: ignore
            self.prompt_adapter_prompt_mapping.clear()  # type: ignore

        def __init__(
            self,
            *,
            # From sequence group metadata.
            request_id: str,
            seq_ids: List[int],
            is_prompt: bool,
            block_tables: Optional[Dict[int, List[int]]],
            computed_block_nums: List[int],
            n_seqs: int = 0,

            # Input tokens and positions.
            input_tokens: Optional[List[List[int]]] = None,
            input_positions: Optional[List[List[int]]] = None,
            token_types: Optional[List[List[int]]] = None,
            mrope_input_positions: Optional[List[List[List[int]]]] = None,

            # The sequence length (may be capped to the sliding window).
            seq_lens: Optional[List[int]] = None,
            # The original sequence length (before applying sliding window).
            # This is used to compute slot mapping.
            orig_seq_lens: Optional[List[int]] = None,
            # The query length.
            query_lens: Optional[List[int]] = None,
            # The number of tokens that are already computed.
            context_lens: Optional[List[int]] = None,
            # The current sliding window block.
            curr_sliding_window_blocks: Optional[List[int]] = None,

            # LoRA inputs.
            lora_index_mapping: Optional[List[List[int]]] = None,
            lora_prompt_mapping: Optional[List[List[int]]] = None,
            lora_requests: Optional[Set[LoRARequest]] = None,

            # Prompt adapter inputs.
            prompt_adapter_index_mapping: Optional[List[int]] = None,
            prompt_adapter_prompt_mapping: Optional[List[int]] = None,
            prompt_adapter_request: Optional[PromptAdapterRequest] = None,

            # Multi-modal inputs.
            multi_modal_kwargs: Optional[MultiModalKwargs] = None,
            multi_modal_placeholder_maps: Optional[Dict[
                str, MultiModalPlaceholderMap]] = None,

            # Whether the prefix cache is hit (prefill only).
            prefix_cache_hit: bool = False,
            reinit: bool = False,
            reinit_use_defaults: bool = False,
            encoder_seq_len: int = 0,
        ):
            if reinit:
                assert len(self.seq_ids) == len(seq_ids)  # type: ignore
                for i, seq_id in enumerate(seq_ids):
                    self.seq_ids[i] = seq_id  # type: ignore
            else:
                self.seq_ids = seq_ids

            self.request_id = request_id
            self.is_prompt = is_prompt
            self.block_tables = block_tables
            self.computed_block_nums = computed_block_nums
            self.n_seqs = n_seqs
            self.encoder_seq_len = encoder_seq_len

            if reinit:
                if len(self.seq_ids) == 1 and reinit_use_defaults:
                    self.simple_reinit()
                else:
                    if input_tokens:
                        self.input_tokens = input_tokens
                    else:
                        for seq_id in range(len(self.seq_ids)):
                            self.input_tokens[seq_id].clear()

                    if input_positions:
                        self.input_positions = input_positions
                    else:
                        for seq_id in range(len(self.seq_ids)):
                            self.input_positions[seq_id].clear()

                    if token_types:
                        self.token_types = token_types
                    else:
                        for seq_id in range(len(self.seq_ids)):
                            self.token_types[seq_id].clear()

                    self.mrope_input_positions = None

                    if seq_lens:
                        self.seq_lens = seq_lens
                    else:
                        for seq_id in range(len(self.seq_ids)):
                            self.seq_lens[seq_id] = 0

                    if orig_seq_lens:
                        self.orig_seq_lens = orig_seq_lens
                    else:
                        for seq_id in range(len(self.seq_ids)):
                            self.orig_seq_lens[seq_id] = 0

                    if query_lens:
                        self.query_lens = query_lens
                    else:
                        for seq_id in range(len(self.seq_ids)):
                            self.query_lens[seq_id] = 0

                    if context_lens:
                        self.context_lens = context_lens
                    else:
                        for seq_id in range(len(self.seq_ids)):
                            self.context_lens[seq_id] = 0

                    if curr_sliding_window_blocks:
                        self.curr_sliding_window_blocks = \
                            curr_sliding_window_blocks
                    else:
                        for seq_id in range(len(self.seq_ids)):
                            self.curr_sliding_window_blocks[seq_id] = 0

                    if lora_index_mapping:
                        self.lora_index_mapping = lora_index_mapping
                    else:
                        self.lora_index_mapping.clear()

                    if lora_prompt_mapping:
                        self.lora_prompt_mapping = lora_prompt_mapping
                    else:
                        self.lora_prompt_mapping.clear()

                    if lora_requests:
                        self.lora_requests = lora_requests
                    else:
                        self.lora_requests.clear()

                    if prompt_adapter_index_mapping:
                        self.prompt_adapter_index_mapping = \
                            prompt_adapter_index_mapping
                    else:
                        self.prompt_adapter_index_mapping.clear()

                    if prompt_adapter_prompt_mapping:
                        self.prompt_adapter_prompt_mapping = \
                            prompt_adapter_prompt_mapping
                    else:
                        self.prompt_adapter_prompt_mapping.clear()

            else:
                self.input_tokens = input_tokens or []
                self.input_positions = input_positions or []
                self.token_types = token_types or []
                self.mrope_input_positions = mrope_input_positions or None
                self.seq_lens = seq_lens or []
                self.orig_seq_lens = orig_seq_lens or []
                self.query_lens = query_lens or []
                self.context_lens = context_lens or []
                self.curr_sliding_window_blocks = \
                    curr_sliding_window_blocks or []

                self.lora_index_mapping = lora_index_mapping or []
                self.lora_prompt_mapping = lora_prompt_mapping or []
                self.lora_requests = lora_requests or set()

                self.prompt_adapter_index_mapping = (
                    prompt_adapter_index_mapping or [])
                self.prompt_adapter_prompt_mapping = (
                    prompt_adapter_prompt_mapping or [])

            self.prompt_adapter_request = prompt_adapter_request
            self.multi_modal_kwargs = multi_modal_kwargs
            self.multi_modal_placeholder_maps = multi_modal_placeholder_maps
            self.prefix_cache_hit = prefix_cache_hit

            self.n_seqs = len(self.seq_ids)

            if not reinit:
                self.__post_init__()

        def __post_init__(self):
            self.n_seqs = len(self.seq_ids)

            self.input_tokens = [[] for _ in range(self.n_seqs)]
            self.input_positions = [[] for _ in range(self.n_seqs)]
            self.token_types = [[] for _ in range(self.n_seqs)]
            self.mrope_input_positions = None
            self.seq_lens = [0] * self.n_seqs
            self.orig_seq_lens = [0] * self.n_seqs
            self.query_lens = [0] * self.n_seqs
            self.context_lens = [0] * self.n_seqs
            self.curr_sliding_window_blocks = [0] * self.n_seqs

            self.lora_index_mapping = []
            self.lora_prompt_mapping = []


class NPUModelRunner(ModelRunner):
    """
    NPU model runner with sampling step.
    """
    _model_input_cls: Type[ModelInputForGPUWithSamplingMetadata] = (
        ModelInputForGPUWithSamplingMetadata)
    _builder_cls: Type[ModelInputForNPUBuilder] = ModelInputForNPUBuilder

    def make_model_input_from_broadcasted_tensor_dict(
        self,
        tensor_dict: Dict[str, Any],
    ) -> ModelInputForGPUWithSamplingMetadata:
        model_input = \
            ModelInputForGPUWithSamplingMetadata.from_broadcasted_tensor_dict(
                tensor_dict,
                attn_backend=self.attn_backend,
            )
        return model_input

    def _create_dummy_kv_cache(self, attn_metadata, input_ids):
        """
        Creates a dummy key-value cache for attention during warmup phase.

        Args:
            attn_metadata (AttentionMetadata): Metadata related to attention for the current batch.
            input_ids (torch.Tensor): Input token IDs for the current batch.

        Returns:
            Tuple: A tuple containing the key-value cache, block tables, and slot mappings.
        """
        dummy_block_size = 128
        max_s = max(attn_metadata.prefill_metadata.seq_lens)
        max_need_block = math.ceil(max_s / dummy_block_size)
        batch_size = len(attn_metadata.prefill_metadata.seq_lens)

        head_size = self.model_config.get_head_size()
        num_kv_heads = self.model_config.get_num_kv_heads(self.parallel_config)
        dummy_kv_cache_shape = self.attn_backend.get_kv_cache_shape(
            self.dummy_block_num, self.dummy_block_size, num_kv_heads,
            head_size)
        num_layers = self.model_config.get_num_layers(self.parallel_config)
        kv_cache_torch_dtype = get_kv_cache_torch_dtype(
            self.kv_cache_dtype, self.model_config.dtype)
        kv_cache = [
            torch.empty(
                size=dummy_kv_cache_shape,
                dtype=kv_cache_torch_dtype,
                device="npu",
            ) for _ in range(num_layers)
        ]

        block_tables = torch.zeros(batch_size,
                                   max_need_block,
                                   dtype=torch.int32).to(device="npu",
                                                         non_blocking=True)
        slot = [i for i in range(dummy_block_size)]
        slots = []
        warm_up_len = len(input_ids)
        while warm_up_len > 0:
            if warm_up_len > dummy_block_size:
                slots.extend(slot)
                warm_up_len -= dummy_block_size
            else:
                slots.extend(slot[:warm_up_len])
                warm_up_len = 0
        slots = torch.tensor(slots, dtype=torch.int32).to(device="npu",
                                                          non_blocking=True)
        return kv_cache, block_tables, slots

    @current_platform.inference_mode()
    def profile_run(self) -> None:
        # Enable top-k sampling to reflect the accurate memory usage.
        sampling_params = SamplingParams(top_p=0.99, top_k=self.vocab_size - 1)
        max_num_batched_tokens = self.scheduler_config.max_num_batched_tokens
        max_num_seqs = self.scheduler_config.max_num_seqs
        # This represents the maximum number of different requests
        # that will have unique loras, an therefore the max amount of memory
        # consumption create dummy lora request copies from the lora request
        # passed in, which contains a lora from the lora warmup path.
        dummy_lora_requests: List[LoRARequest] = []
        dummy_lora_requests_per_seq: List[LoRARequest] = []
        if self.lora_config:
            assert self.lora_manager is not None
            with self.lora_manager.dummy_lora_cache():
                for idx in range(self.lora_config.max_loras):
                    lora_id = idx + 1
                    dummy_lora_request = LoRARequest(
                        lora_name=f"warmup_{lora_id}",
                        lora_int_id=lora_id,
                        lora_path="/not/a/real/path",
                    )
                    self.lora_manager.add_dummy_lora(dummy_lora_request,
                                                     rank=LORA_WARMUP_RANK)
                    dummy_lora_requests.append(dummy_lora_request)
                dummy_lora_requests_per_seq = [
                    dummy_lora_requests[idx % len(dummy_lora_requests)]
                    for idx in range(max_num_seqs)
                ]

        # Profile memory usage with max_num_sequences sequences and the total
        # number of tokens equal to max_num_batched_tokens.
        seqs: List[SequenceGroupMetadata] = []
        # Additional GPU memory may be needed for multi-modal encoding, which
        # needs to be accounted for when calculating the GPU blocks for
        # vLLM blocker manager.
        # To exercise the worst scenario for GPU memory consumption,
        # the number of seqs (batch_size) is chosen to maximize the number
        # of images processed.

        max_mm_tokens = self.mm_registry.get_max_multimodal_tokens(
            self.model_config)
        if max_mm_tokens > 0:
            max_num_seqs_orig = max_num_seqs
            max_num_seqs = min(max_num_seqs,
                               max_num_batched_tokens // max_mm_tokens)
            if max_num_seqs < 1:
                expr = (f"min({max_num_seqs_orig}, "
                        f"{max_num_batched_tokens} // {max_mm_tokens})")
                logger.warning(
                    "Computed max_num_seqs (%s) to be less than 1. "
                    "Setting it to the minimum value of 1.", expr)
                max_num_seqs = 1

        batch_size = 0
        max_seq_len = 0
        for group_id in range(max_num_seqs):
            seq_len = (max_num_batched_tokens // max_num_seqs +
                       (group_id < max_num_batched_tokens % max_num_seqs))
            batch_size += seq_len
            max_seq_len = max(max_seq_len, seq_len)

            dummy_data = self.input_registry \
                .dummy_data_for_profiling(self.model_config,
                                          seq_len,
                                          self.mm_registry)

            seq = SequenceGroupMetadata(
                request_id=str(group_id),
                is_prompt=True,
                seq_data={group_id: dummy_data.seq_data},
                sampling_params=sampling_params,
                block_tables=None,
                lora_request=dummy_lora_requests_per_seq[group_id]
                if dummy_lora_requests_per_seq else None,
                multi_modal_data=dummy_data.multi_modal_data,
                multi_modal_placeholders=dummy_data.multi_modal_placeholders,
            )
            seqs.append(seq)

        self.dummy_block_size = 128
        self.dummy_block_num = max_num_seqs * math.ceil(
            max_seq_len / self.dummy_block_size)
        # Run the model with the dummy inputs.
        finished_requests_ids = [seq.request_id for seq in seqs]
        model_input = self.prepare_model_input(
            seqs, finished_requests_ids=finished_requests_ids)
        kv_caches, block_tables, slots = self._create_dummy_kv_cache(
            model_input.attn_metadata, model_input.input_tokens)
        model_input.attn_metadata.prefill_metadata.block_tables = block_tables
        model_input.attn_metadata.slot_mapping = slots
        model_input.attn_metadata.dummy_kv_caches = kv_caches
        intermediate_tensors = None
        if not get_pp_group().is_first_rank:
            intermediate_tensors = self.model.make_empty_intermediate_tensors(
                batch_size=batch_size,
                dtype=self.model_config.dtype,
                device=self.device)
        self.execute_model(model_input, kv_caches, intermediate_tensors)
        current_platform.synchronize()
        return

    @current_platform.inference_mode()
    def capture_model(self, kv_caches: List[List[torch.Tensor]]) -> None:
        """NPU graph capture a model.
        TODO: not support now
        """
        pass

    @torch.inference_mode()
    @dump_input_when_exception(exclude_args=[0], exclude_kwargs=["self"])
    def execute_model(
        self,
        model_input: ModelInputForGPUWithSamplingMetadata,
        kv_caches: List[torch.Tensor],
        intermediate_tensors: Optional[IntermediateTensors] = None,
        num_steps: int = 1,
    ) -> Optional[Union[List[SamplerOutput], IntermediateTensors]]:
        if num_steps > 1:
            raise ValueError("num_steps > 1 is not supported in ModelRunner")

        if self.lora_config:
            assert model_input.lora_requests is not None
            assert model_input.lora_mapping is not None
            self.set_active_loras(model_input.lora_requests,
                                  model_input.lora_mapping)

        if self.prompt_adapter_config:
            assert model_input.prompt_adapter_requests is not None
            assert model_input.prompt_adapter_mapping is not None
            self.set_active_prompt_adapters(
                model_input.prompt_adapter_requests,
                model_input.prompt_adapter_mapping)

        self.attn_state.begin_forward(model_input)

        assert model_input.attn_metadata is not None
        # TODO(andoorve): We can remove this once all
        # virtual engines share the same kv cache.
        virtual_engine = model_input.virtual_engine
        model_executable = self.model

        # Receive KV cache in distributed KV cache transfer setting
        # In disagg prefill setting, it will also recv hidden states and bypass
        # model forwarding
        # In KV cache database setting, it will change the model input so that
        # we can skip prefilling on tokens that successfully received KV caches
        # NOTE: The receive operation is blocking
        bypass_model_exec = False
        if self.need_recv_kv(model_input, kv_caches):
            hidden_or_intermediate_states, bypass_model_exec, model_input = \
                get_kv_transfer_group().recv_kv_caches_and_hidden_states(
                    # model is used to know which layer the current worker
                    # is working on, so that we can receive KV for only those
                    # layers.
                    model_executable,
                    model_input,
                    kv_caches=kv_caches
                )

        multi_modal_kwargs = model_input.multi_modal_kwargs or {}
        seqlen_agnostic_kwargs = {
            "finished_requests_ids": model_input.finished_requests_ids,
            "request_ids_to_seq_ids": model_input.request_ids_to_seq_ids,
        } if self.has_inner_state else {}
        if (self.observability_config is not None
                and self.observability_config.collect_model_forward_time):
            model_forward_start = torch.npu.Event(enable_timing=True)
            model_forward_end = torch.npu.Event(enable_timing=True)
            model_forward_start.record()

        if not bypass_model_exec:
            with set_forward_context(model_input.attn_metadata,
                                     self.vllm_config, virtual_engine):
                hidden_or_intermediate_states = model_executable(
                    input_ids=model_input.input_tokens,
                    positions=model_input.input_positions,
                    kv_caches=kv_caches,
                    attn_metadata=model_input.attn_metadata,
                    intermediate_tensors=intermediate_tensors,
                    **MultiModalKwargs.as_kwargs(multi_modal_kwargs,
                                                 device=self.device),
                    **seqlen_agnostic_kwargs)

        if (self.observability_config is not None
                and self.observability_config.collect_model_forward_time):
            model_forward_end.record()

        # Sending KV cache in distributed KV cache transfer setting
        # NOTE: the send operation is non-blocking
        if self.need_send_kv(model_input, kv_caches):
            get_kv_transfer_group().send_kv_caches_and_hidden_states(
                # model_executable is used to know which layer the current
                # worker is working on, so that we can send KV for only those
                # layers.
                model_executable,
                model_input,
                kv_caches,
                hidden_or_intermediate_states,
            )

        # Compute the logits in the last pipeline stage.
        if not get_pp_group().is_last_rank:
            if (self.is_driver_worker
                    and hidden_or_intermediate_states is not None
                    and isinstance(hidden_or_intermediate_states,
                                   IntermediateTensors)
                    and self.observability_config is not None
                    and self.observability_config.collect_model_forward_time):
                model_forward_end.synchronize()
                model_forward_time = model_forward_start.elapsed_time(
                    model_forward_end)
                orig_model_forward_time = 0.0
                if intermediate_tensors is not None:
                    orig_model_forward_time = intermediate_tensors.tensors.get(
                        "model_forward_time", torch.tensor(0.0)).item()
                hidden_or_intermediate_states.tensors["model_forward_time"] = (
                    torch.tensor(model_forward_time + orig_model_forward_time))
            return hidden_or_intermediate_states

        logits = self.model.compute_logits(hidden_or_intermediate_states,
                                           model_input.sampling_metadata)

        if not self.is_driver_worker:
            return []

        if model_input.async_callback is not None:
            model_input.async_callback()

        # Sample the next token.
        output: SamplerOutput = self.model.sample(
            logits=logits,
            sampling_metadata=model_input.sampling_metadata,
        )
        if (self.observability_config is not None
                and self.observability_config.collect_model_forward_time
                and output is not None):
            model_forward_end.synchronize()
            model_forward_time = model_forward_start.elapsed_time(
                model_forward_end)
            orig_model_forward_time = 0.0
            if intermediate_tensors is not None:
                orig_model_forward_time = intermediate_tensors.tensors.get(
                    "model_forward_time", torch.tensor(0.0)).item()
            # If there are multiple workers, we are still tracking the latency
            # from the start time of the driver worker to the end time of the
            # driver worker. The model forward time will then end up covering
            # the communication time as well.
            output.model_forward_time = (orig_model_forward_time +
                                         model_forward_time)

        if self.return_hidden_states:
            # we only need to pass hidden states of most recent token
            assert model_input.sampling_metadata is not None
            indices = model_input.sampling_metadata.selected_token_indices
            if model_input.is_prompt:
                hidden_states = hidden_or_intermediate_states.index_select(
                    0, indices)
                output.prefill_hidden_states = hidden_or_intermediate_states
            else:
                hidden_states = hidden_or_intermediate_states

            output.hidden_states = hidden_states

        return [output]

    def prepare_model_input(
        self,
        seq_group_metadata_list: List[SequenceGroupMetadata],
        virtual_engine: int = 0,
        finished_requests_ids: Optional[List[str]] = None,
    ) -> ModelInputForGPUWithSamplingMetadata:
        """Prepare the model input based on a given sequence group, including
        metadata for the sampling step.
        The API assumes seq_group_metadata_list is sorted by prefill -> decode.
        The result tensors and data structure also batches input in prefill
        -> decode order. For example,
        - input_tokens[:num_prefill_tokens] contains prefill tokens.
        - input_tokens[num_prefill_tokens:] contains decode tokens.
        """
        model_input = self._prepare_model_input_tensors(
            seq_group_metadata_list, finished_requests_ids)
        if get_pp_group().is_last_rank:
            # Sampling metadata is only required for the final pp group
            generators = self.get_generators(finished_requests_ids)
            sampling_metadata = SamplingMetadata.prepare(
                seq_group_metadata_list,
                model_input.seq_lens,
                model_input.query_lens,
                self.device,
                self.pin_memory,
                generators,
                self.sampling_metadata_cache,
                # TODO (cmq): enable this after supported in vllm
                # pad_for_invariant_seq_len=True,
            )
        else:
            sampling_metadata = None
        is_prompt = (seq_group_metadata_list[0].is_prompt
                     if seq_group_metadata_list else None)
        return dataclasses.replace(model_input,
                                   sampling_metadata=sampling_metadata,
                                   is_prompt=is_prompt,
                                   virtual_engine=virtual_engine)
