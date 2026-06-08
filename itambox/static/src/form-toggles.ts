/**
 * ITAMbox — Form Field Toggles.
 *
 * Dynamically toggles field visibility in forms, such as report schedules,
 * asset requests, and purchase order lines.
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

  function toggleDisplay(id: string, show: boolean) {
    const el = document.getElementById(id);
    if (el) el.style.display = show ? '' : 'none';
  }

  function toggleChannelFields() {
    const select = document.getElementById('id_channel_type') as HTMLSelectElement | null;
    if (!select) return;
    const type = select.value;
    toggleDisplay('div_id_webhook_url', type === 'slack' || type === 'teams');
    toggleDisplay('div_id_email_recipients', type === 'email');
    toggleDisplay('div_id_in_app_recipient_users', type === 'in_app');
  }

  function toggleCategoryFields() {
    const categorySelect = document.getElementById('id_request_category') as HTMLSelectElement | null;
    if (!categorySelect) return;
    const selected = categorySelect.value;

    const isAssetRequest = !!document.getElementById('div_id_asset');
    const isPOLine = !!document.getElementById('div_id_license');

    if (isAssetRequest) {
      toggleDisplay('div_id_asset_type', false);
      toggleDisplay('div_id_asset', false);
      toggleDisplay('div_id_component', false);
      toggleDisplay('div_id_accessory', false);
      toggleDisplay('div_id_consumable', false);

      const showQty = ['asset_type', 'component', 'accessory', 'consumable'].includes(selected);
      toggleDisplay('div_id_qty', showQty);

      if (selected === 'asset_type') {
        toggleDisplay('div_id_asset_type', true);
      } else if (selected === 'asset') {
        toggleDisplay('div_id_asset', true);
      } else if (selected === 'component') {
        toggleDisplay('div_id_component', true);
      } else if (selected === 'accessory') {
        toggleDisplay('div_id_accessory', true);
      } else if (selected === 'consumable') {
        toggleDisplay('div_id_consumable', true);
      }
    } else if (isPOLine) {
      toggleDisplay('div_id_asset_type', false);
      toggleDisplay('div_id_component', false);
      toggleDisplay('div_id_accessory', false);
      toggleDisplay('div_id_consumable', false);
      toggleDisplay('div_id_license', false);

      if (selected === 'asset_type') {
        toggleDisplay('div_id_asset_type', true);
      } else if (selected === 'component') {
        toggleDisplay('div_id_component', true);
      } else if (selected === 'accessory') {
        toggleDisplay('div_id_accessory', true);
      } else if (selected === 'consumable') {
        toggleDisplay('div_id_consumable', true);
      } else if (selected === 'license') {
        toggleDisplay('div_id_license', true);
      }
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    initScheduleForm();
    toggleCategoryFields();
    toggleChannelFields();
  });

  document.body.addEventListener("htmx:afterSettle", () => {
    initScheduleForm();
    toggleCategoryFields();
    toggleChannelFields();
  });

  document.body.addEventListener("shown.bs.modal", () => {
    toggleCategoryFields();
    toggleChannelFields();
  });

  document.body.addEventListener("change", (e) => {
    const target = e.target as HTMLSelectElement;
    if (!target) return;
    if (target.name === 'frequency') {
      toggleScheduleFields(target);
    }
    if (target.id === 'id_request_category' || target.name === 'request_category' || target.name === 'item_category') {
      toggleCategoryFields();
    }
    if (target.id === 'id_channel_type' || target.name === 'channel_type') {
      toggleChannelFields();
    }
  });
})();
