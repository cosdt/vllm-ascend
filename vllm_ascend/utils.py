from vllm.logger import init_logger

logger = init_logger(__name__)


def try_register_lib(lib_name: str, lib_info: str = None):
    import importlib
    try:
        module_spec = importlib.util.find_spec(lib_name)
        if module_spec is not None:
            importlib.import_module(lib_name)
            if lib_info is not None:
                logger.info(lib_info)
    except Exception:
        pass
