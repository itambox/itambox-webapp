"""Quick-onboard forms for managing (MSP) organizations — retired in stage 3.

``TechnicianQuickForm`` is gone: technician onboarding is now the unified
"Add member" flow (``MembershipForm`` in ``membership_form.py``), reached via
``memberships/add/?tenant=<msp pk>&preset=technician``. The old
``onboard/technician/`` route (``TechnicianQuickAddView`` in
``views/provider_views.py``) survives only as a thin redirect to that URL.

The module is kept (empty) so the historical path stays discoverable; new
provider-onboarding form logic belongs in ``membership_form.py``.
"""
