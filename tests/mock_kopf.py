import sys


# Create a simple mock kopf module
class MockKopf:
    class TemporaryError(Exception):
        def __init__(self, message, delay=60):
            self.message = message
            self.delay = delay
            super().__init__(message)

    class _OnNamespace:
        @staticmethod
        def startup():
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def cleanup():
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def create(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def update(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def delete(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def field(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def resume(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

    on = _OnNamespace()

    @staticmethod
    def on_startup():
        def decorator(func):
            return func

        return decorator

    @staticmethod
    def on_cleanup():
        def decorator(func):
            return func

        return decorator

    class OperatorSettings:
        def __init__(self):
            self.health = type("obj", (object,), {"server": "0.0.0.0", "port": 8080})()
            self.peering = type(
                "obj", (object,), {"name": "titlis-operator", "namespace": "titlis"}
            )()


# Inject the mock before any real imports
sys.modules["kopf"] = MockKopf()
