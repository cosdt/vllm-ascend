import torch
from vllm.model_executor.layers.activation import SiluAndMul


def silu_and_mul_forward_oot(self, x: torch.Tensor) -> torch.Tensor:
    import torch_npu

    out = torch_npu.npu_swiglu(x)
    return out


SiluAndMul.forward_oot = silu_and_mul_forward_oot
