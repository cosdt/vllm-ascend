from typing import Optional, Tuple

import torch
from vllm.model_executor.layers.rotary_embedding import RotaryEmbedding


def rope_forward_oot(
    self,
    positions: torch.Tensor,
    query: torch.Tensor,
    key: torch.Tensor,
    offsets: Optional[torch.Tensor] = None,
) -> Tuple[torch.Tensor, torch.Tensor]:
    import torch_npu

    self.cos_sin_cache = self.cos_sin_cache.to(query.device, dtype=query.dtype)
    if offsets is not None:
        raise NotImplementedError(
            "Batched rotary embedding is currently not supported on NPU.")
    else:
        query = query.contiguous()
        key = key.contiguous()
        torch_npu.npu_rope(
            positions,
            query,
            key,
            self.head_size,
            self.cos_sin_cache,
            self.is_neox_style,
        )

    return query, key


RotaryEmbedding.forward_oot = rope_forward_oot