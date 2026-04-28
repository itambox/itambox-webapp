from .providers import LocalSignatureProvider

class SignatureProviderRegistry:
    def __init__(self):
        self._registry = {}

    def register(self, provider_class):
        self._registry[provider_class.name] = provider_class()

    def get(self, name):
        return self._registry.get(name)

    def choices(self):
        return [(k, v.verbose_name) for k, v in self._registry.items()]


signature_providers = SignatureProviderRegistry()
signature_providers.register(LocalSignatureProvider)
