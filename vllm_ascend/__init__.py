def register():
    """Register the NPU platform."""
    try:
        from mindie_turbo import vllm_turbo # noqa: F401
    except ImportError:
        from vllm.logger import init_logger
        logger = init_logger(__name__)
        logger.info("MindIE Turbo is not installed. Running vllm without turbo.")
    return "vllm_ascend.platform.NPUPlatform"
