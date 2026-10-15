"""Production activation boundary until the split release architecture lands."""

ACTIVATION_UNAVAILABLE = "Production activation is unavailable after the Boxie split; split-service release/deployment support lands in PR4. Use the documented local Compose units for validation."


class ActivationUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__(ACTIVATION_UNAVAILABLE)
