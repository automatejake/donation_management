/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";

publicWidget.registry.DonationForm = publicWidget.Widget.extend({
    selector: '.donation-form',
    events: {
        'change input[name="amount_preset"]': '_onAmountPresetChange',
        'change #is_recurring': '_onRecurringChange',
        'input #amount': '_onAmountInput',
    },

    /**
     * @override
     */
    start: function () {
        this._super.apply(this, arguments);
        
        // Initialize amount field
        this._updateAmountField();
        
        // Initialize recurring options visibility
        this._updateRecurringOptions();
    },

    /**
     * Handle preset amount button clicks
     */
    _onAmountPresetChange: function (ev) {
        const value = $(ev.currentTarget).val();
        const $amountInput = this.$('#amount');
        
        if (value === 'custom') {
            $amountInput.val('').focus();
        } else {
            $amountInput.val(value);
        }
    },

    /**
     * Handle manual amount input
     */
    _onAmountInput: function (ev) {
        const customRadio = this.$('#amount_custom');
        if (!customRadio.prop('checked')) {
            customRadio.prop('checked', true);
        }
    },

    /**
     * Handle recurring checkbox change
     */
    _onRecurringChange: function (ev) {
        this._updateRecurringOptions();
    },

    /**
     * Update amount field based on preset selection
     */
    _updateAmountField: function () {
        const checkedPreset = this.$('input[name="amount_preset"]:checked');
        if (checkedPreset.length && checkedPreset.val() !== 'custom') {
            this.$('#amount').val(checkedPreset.val());
        }
    },

    /**
     * Show/hide recurring donation options
     */
    _updateRecurringOptions: function () {
        const isRecurring = this.$('#is_recurring').is(':checked');
        const $recurringOptions = this.$('#recurring_options');
        const $saveTokenOption = this.$('#save_token_option');
        
        if (isRecurring) {
            $recurringOptions.slideDown();
            $saveTokenOption.show();
            this.$('#save_token').prop('checked', true);
        } else {
            $recurringOptions.slideUp();
            $saveTokenOption.hide();
        }
    },
});

export default publicWidget.registry.DonationForm;