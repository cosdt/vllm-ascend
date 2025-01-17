def register():
    """Register the NPU platform."""
    register_mindie_turbo()
    return "vllm_ascend.platform.NPUPlatform"


# flake8: noqa: F401
def register_mindie_turbo():
    try:
        from mindie_turbo import vllm_turbo  # type: ignore
    except ImportError:
        from vllm.logger import init_logger
        logger = init_logger(__name__)
        logger.info(
            "MindIE Turbo is not installed. Running vllm without turbo.")
