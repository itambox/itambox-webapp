"""Journey guardrail suite: cross-module business invariants.

The repository's static gates constrain code shape; they cannot tell that two
modules disagree about a business fact. Each module here walks one user journey
end to end and asserts the CORRECT behaviour. While a defect is open the test
carries ``xfail(strict=True)`` naming the owning issue, so the fix PR flips it
to passing by deleting the marker -- and a test that starts passing early fails
the run until its marker is removed.

Journey tests grant permissions through roles (``grant`` /
``TenantTestMixin.client_login_to_tenant``). Never use superusers or Django
``user_permissions``: that is how the catalogue permission defect stayed hidden.
"""
