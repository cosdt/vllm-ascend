"""
Copyright (c) Huawei Technologies Co., Ltd. 2024-2025. All rights reserved.
MindIE is licensed under Mulan PSL v2.
You can use this software according to the terms and conditions of the Mulan PSL v2.
You may obtain a copy of Mulan PSL v2 at:
         http://license.coscl.org.cn/MulanPSL2
THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
See the Mulan PSL v2 for more details.
"""

from typing import Any, Dict, List, Optional

import torch
import torch_npu

from vllm import _custom_ops as ops
from vllm.logger import init_logger
from vllm.model_executor.layers.linear import (LinearBase, LinearMethodBase,
                                               UnquantizedLinearMethod, RowParallelLinear)
from vllm.model_executor.layers.quantization import (register_quantization_config)
from vllm.model_executor.layers.quantization.base_config import (QuantizationConfig)
from vllm.model_executor.parameter import (BasevLLMParameter,
                                           GroupQuantScaleParameter,
                                           PackedvLLMParameter,
                                           ModelWeightParameter)
try:
    from mindie_turbo import get_quantizer, BaseQuantizer
except:
    pass

logger = init_logger(__name__)

@register_quantization_config("ascend")
class AscendConfig(QuantizationConfig):
    """Config class for Ascend"""

    def __init__(self, quantizer: BaseQuantizer):
        self.quantizer = quantizer

    def __repr__(self) -> str:
        return "AscendConfig:\n" + super().__repr__()

    @classmethod
    def get_name(cls) -> str:
        return "ascend"

    @classmethod
    def get_supported_act_dtypes(cls) -> List[torch.dtype]:
        return [torch.half, torch.bfloat16]

    @classmethod
    def get_min_capability(cls) -> int:
        return 80

    @classmethod
    def get_config_filenames(cls) -> List[str]:
        return ["quantize_config.json"]

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "AscendConfig":
        return cls(get_quantizer(config))

    @classmethod
    def override_quantization_method(cls, hf_quant_cfg,
                                     user_quant) -> Optional[str]:
        dev_type = hf_quant_cfg.get("dev_type", None)
        if dev_type == "npu":
            return "ascend"
        return None

    def get_quant_method(self, layer: torch.nn.Module,
                         prefix: str) -> Optional["QuantizeMethodBase"]:
        if isinstance(layer, LinearBase):
            return AscendLinearMethod(self.quantizer)
        return None

    def get_scaled_act_names(self) -> List[str]:
        return []


class AscendLinearMethod(LinearMethodBase):
    """Linear method for Ascend quantization.

    Args:
        quantizer: The Ascend quantization interface.
    """

    def __init__(self, quantizer: BaseQuantizer) -> None:
        self.quantizer = quantizer

    def create_weights(
        self,
        layer: torch.nn.Module,
        input_size_per_partition: int,
        output_partition_sizes: List[int],
        input_size: int,
        output_size: int,
        params_dtype: torch.dtype,
        **extra_weight_attrs,
    ) -> None:
        del output_size
        output_size_per_partition = sum(output_partition_sizes)
        weight_loader = extra_weight_attrs.get("weight_loader")

        # Normalize group_size
        # if self.quantizer.group_size <= 0:
        #     group_size = self.quant_config.group_size
        # else:
        #     group_size = input_size

        layer.input_size = input_size_per_partition
        layer.output_size = output_size_per_partition
        
        self.quantizer.register_linear_parameter(
            layer,
            layer.input_size,
            layer.output_size,
            params_dtype
        )
    
        weight = self.quantizer.get_weight(layer)
        if isinstance(layer, RowParallelLinear):
            layer.register_parameter(
                "weight",
                ModelWeightParameter(
                    data=self.quantizer.get_weight(layer).data.transpose(0, 1),
                    input_dim=1,
                    output_dim=0,
                    weight_loader=weight_loader
                )
            )
        else:
            layer.register_parameter(
                "weight",
                ModelWeightParameter(
                    data=self.quantizer.get_weight(layer).data,
                    input_dim=0,
                    output_dim=1,
                    weight_loader=weight_loader
                )
            )
        
        pertensor_params = self.quantizer.get_pertensor_param(layer)
        for name, param in pertensor_params.items():
            layer.register_parameter(
                name,
                BasevLLMParameter(
                    data=param.data,
                    weight_loader=weight_loader
                )
            )
        
        perchannel_params = self.quantizer.get_perchannel_param(layer)
        for name, param in perchannel_params.items():
            layer.register_parameter(
                name,
                GroupQuantScaleParameter(
                    data=param.data,
                    input_dim=0,
                    output_dim=0,
                    weight_loader=weight_loader
                )
            )

    def process_weights_after_loading(self, layer: torch.nn.Module) -> None:
        if isinstance(layer, RowParallelLinear):
            layer.weight.data = layer.weight.data.transpose(1, 0)
        layer.input_scale = torch.nn.Parameter(torch.broadcast_to(layer.input_scale, (layer.input_size, )), requires_grad=False)
        layer.input_offset = torch.nn.Parameter(torch.broadcast_to(layer.input_offset, (layer.input_size, )), requires_grad=False)
        
    def apply(
        self,
        layer: torch.nn.Module,
        x: torch.Tensor,
        bias: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        return self.quantizer.linear_quant_method.apply(layer, x, bias)