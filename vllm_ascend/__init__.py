def register():
    """Register the NPU platform."""
    try:
        from mindie_turbo import vllm_turbo
    except ImportError:
        import logging
        logging.info("MindIE Turbo is not installed. Running vllm without turbo.")
    return "vllm_ascend.platform.NPUPlatform"
