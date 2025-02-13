import getpass
import inspect
import logging
import os
import sys
from datetime import datetime
from logging.handlers import WatchedFileHandler


def get_standard_logger(progress_logger_stderr=True, persist=True):
    """

    :param bool progress_logger_stderr: Simple logger that gives progress updates to person running script
    :param bool|basestring persist:
    :return:
    """
    parent_caller = inspect.currentframe().f_back
    file_name, _, _, _, _ = inspect.getframeinfo(parent_caller)

    logger = logging.getLogger(file_name)
    logger.setLevel(logging.DEBUG)

    log_file_format = logging.Formatter(
        fmt="[%(asctime)s] - %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    if progress_logger_stderr:
        stderr_channel = logging.StreamHandler()
        stderr_channel.setLevel(logging.DEBUG)
        stderr_channel.setFormatter(log_file_format)
        logger.addHandler(stderr_channel)

    if persist:
        working_dir = os.path.dirname(os.path.realpath(__file__))
        log_directory = os.path.join(working_dir.split("pats")[0], "logs")
        if not os.path.exists(log_directory):
            try:
                os.makedirs(log_directory)
            except OSError:
                logger.warning(
                    "Cannot persist logs due to permission error. Continuing without saving logs"
                )
                return logger

        if isinstance(persist, str):
            directory_channel = WatchedFileHandler(os.path.join(log_directory, persist))
        else:
            program_name = sys.argv[0].replace(".", "_")
            user_name = getpass.getuser().replace(".", "_")
            current_time = datetime.now().strftime("%Y_%m_%d_%H%M%S")
            directory_channel = WatchedFileHandler(
                os.path.join(log_directory, f"{program_name}_{user_name}-{current_time}.log")
            )

        directory_channel.setLevel(logging.DEBUG)
        directory_channel.setFormatter(log_file_format)
        logger.addHandler(directory_channel)

    return logger


def log_error_and_exit(logger, log_msg="", exit_code=1):
    """Log error message and terminate program"""
    logger.error(log_msg)
    sys.exit(exit_code)


def log_exception_and_exit(logger, log_msg="", exit_code=1):
    """Logger will log stack trace error and terminate program"""
    logger.exception(log_msg)
    sys.exit(exit_code)
