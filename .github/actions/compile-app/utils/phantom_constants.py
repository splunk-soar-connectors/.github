import os

DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
os.chdir(DIR)

PHANTOM_INSTANCE_CURRENT_VERSION_IP = os.getenv(
    "PHANTOM_INSTANCE_CURRENT_VERSION_IP", "10.1.65.241"
)
PHANTOM_INSTANCE_PREVIOUS_VERSION_IP = os.getenv(
    "PHANTOM_INSTANCE_PREVIOUS_VERSION_IP", "10.1.18.235"
)

PHANTOM_SSH_USER = os.getenv("PHANTOM_SSH_USER", "phantom")
PHANTOM_PASSWORD = os.getenv("PHANTOM_PASSWORD", "password")
