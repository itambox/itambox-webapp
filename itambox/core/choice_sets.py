"""Kernel-owned ChoiceSet base class."""


class ChoiceSet:
    CHOICES = []

    def __iter__(self):
        yield from [(choice[0], choice[1]) for choice in self.CHOICES]
