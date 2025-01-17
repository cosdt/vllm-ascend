def register():
    """Register the NPU platform."""
    register_mindie_turbo()
    return "vllm_ascend.platform.NPUPlatform"


# flake8: noqa: F401
def register_mindie_turbo():
    turbo_module_name = "mindie_turbo"
    import importlib
    turbo_spec = importlib.util.find_spec(turbo_module_name)
    if turbo_spec is not None:
        importlib.import_module(turbo_module_name)
        from vllm.logger import init_logger
        logger = init_logger(__name__)
        logger.info(
            "MindIE Turbo is installed. vLLM inference efficiency will be accelerated with MindIE Turbo."
        )
