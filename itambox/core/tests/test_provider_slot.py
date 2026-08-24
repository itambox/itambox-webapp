"""Contract tests for the domain-blind single-provider slot."""

import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import IsolatedAsyncioTestCase

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from core.provider_slot import SingleProviderSlot


class SingleProviderSlotTests(SimpleTestCase):
    def test_missing_provider_has_exact_failure(self):
        slot = SingleProviderSlot[object]("tenant access policy")

        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "tenant access policy provider is not configured",
        ):
            slot.get()

    def test_same_object_registration_is_idempotent(self):
        slot = SingleProviderSlot[object]("test")
        provider = object()

        slot.register(provider)
        slot.register(provider)

        self.assertIs(slot.get(), provider)

    def test_different_object_registration_is_rejected_and_original_remains(self):
        slot = SingleProviderSlot[object]("test")
        original = object()
        competing = object()
        slot.register(original)

        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "test provider is already configured with a different object",
        ):
            slot.register(competing)

        self.assertIs(slot.get(), original)

    def test_concurrent_same_object_registration_is_idempotent(self):
        slot = SingleProviderSlot[object]("test")
        provider = object()
        barrier = Barrier(2)

        def register() -> None:
            barrier.wait()
            slot.register(provider)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(register) for _ in range(2)]
            for future in futures:
                future.result()

        self.assertIs(slot.get(), provider)

    def test_concurrent_different_registration_has_one_winner(self):
        slot = SingleProviderSlot[object]("test")
        first = object()
        second = object()
        barrier = Barrier(2)

        def register(provider: object) -> BaseException | None:
            barrier.wait()
            try:
                slot.register(provider)
            except Exception as exc:  # test captures the exact loser
                return exc
            return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            errors = list(executor.map(register, (first, second)))

        self.assertEqual(sum(error is not None for error in errors), 1)
        self.assertIn(slot.get(), (first, second))
        self.assertIsInstance(next(error for error in errors if error is not None), ImproperlyConfigured)

    def test_override_without_process_default(self):
        slot = SingleProviderSlot[object]("test")
        provider = object()

        with slot.override(provider):
            self.assertIs(slot.get(), provider)

        with self.assertRaises(ImproperlyConfigured):
            slot.get()

    def test_nested_override_and_exception_restore_previous_provider(self):
        slot = SingleProviderSlot[object]("test")
        default = object()
        outer = object()
        inner = object()
        slot.register(default)

        with slot.override(outer):
            self.assertIs(slot.get(), outer)
            with slot.override(inner):
                self.assertIs(slot.get(), inner)
            self.assertIs(slot.get(), outer)
            with self.assertRaisesRegex(RuntimeError, "boom"):
                with slot.override(inner):
                    raise RuntimeError("boom")
            self.assertIs(slot.get(), outer)

        self.assertIs(slot.get(), default)

    def test_override_is_contextvar_local_to_normal_threads(self):
        slot = SingleProviderSlot[object]("test")
        default = object()
        override = object()
        slot.register(default)
        observed: list[object] = []

        with slot.override(override):
            with ThreadPoolExecutor(max_workers=1) as executor:
                observed.append(executor.submit(slot.get).result())
            self.assertIs(slot.get(), override)

        self.assertEqual(observed, [default])

    def test_explicit_copy_context_propagates_override_to_thread(self):
        slot = SingleProviderSlot[object]("test")
        default = object()
        override = object()
        slot.register(default)
        observed: list[object] = []

        with slot.override(override):
            context = contextvars.copy_context()
            with ThreadPoolExecutor(max_workers=1) as executor:
                observed.append(executor.submit(context.run, slot.get).result())

        self.assertEqual(observed, [override])

    def test_public_surface_has_no_reset_clear_keys_or_discovery(self):
        slot = SingleProviderSlot[object]("test")

        for name in ("reset", "clear", "keys", "discover", "discover_provider", "providers"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(slot, name))
                self.assertFalse(hasattr(SingleProviderSlot, name))


class SingleProviderSlotAsyncTests(IsolatedAsyncioTestCase):
    async def test_child_task_inherits_override_captured_at_creation(self):
        slot = SingleProviderSlot[object]("test")
        default = object()
        outer = object()
        inner = object()
        slot.register(default)

        async def read_provider() -> object:
            await asyncio.sleep(0)
            return slot.get()

        with slot.override(outer):
            task = asyncio.create_task(read_provider())
            with slot.override(inner):
                self.assertIs(await read_provider(), inner)
            self.assertIs(await task, outer)

        self.assertIs(slot.get(), default)
