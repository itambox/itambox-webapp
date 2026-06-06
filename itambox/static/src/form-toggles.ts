/**
 * ITAMbox — Form Field Toggles.
 *
 * Dynamically toggles field visibility in forms, such as report schedules.
 */
(function () {
  function toggleScheduleFields(select: HTMLSelectElement) {
    const form = select.closest('form');
    if (!form) return;
    const freq = select.value;
    const cronField = form.querySelector('#div_id_cron_expression') as HTMLElement | null;
    const startTimeField = form.querySelector('#div_id_start_time') as HTMLElement | null;

    if (cronField) {
      cronField.style.display = (freq === 'cron') ? '' : 'none';
    }
    if (startTimeField) {
      const showStartTime = ['daily', 'weekly', 'biweekly', 'monthly', 'quarterly', 'yearly'].includes(freq);
      startTimeField.style.display = showStartTime ? '' : 'none';
    }
  }

  function initScheduleForm() {
    const freqSelect = document.querySelector("select[name='frequency']") as HTMLSelectElement | null;
    if (freqSelect) {
      toggleScheduleFields(freqSelect);
    }
  }

  document.addEventListener("DOMContentLoaded", initScheduleForm);
  document.body.addEventListener("htmx:afterSettle", initScheduleForm);

  document.body.addEventListener("change", (e) => {
    const target = e.target as HTMLSelectElement;
    if (target && target.name === 'frequency') {
      toggleScheduleFields(target);
    }
  });
})();
