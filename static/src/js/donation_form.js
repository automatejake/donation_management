/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { rpc } from "@web/core/network/rpc";

publicWidget.registry.DonationForm = publicWidget.Widget.extend({
    selector: '.donation-form',
    events: {
        'change input[name="frequency"]': '_onFrequencyChange',
        'change #recurring_start_date': '_onRecurringStartDateChange',
        'input #amount': '_onAmountInput',
        'change #cover_fees': '_onCoverFeesChange',
        'change input[name="payment_method"]': '_onPaymentMethodChange',
        'blur #email': '_onEmailBlur',
    },

    start: function () {
        this.defaultFeePercent = parseFloat(this.el.dataset.defaultFeePercent || '3.0');
        this.isPublicUser = this.el.dataset.isPublic === '1';
        this.feePercent = this.defaultFeePercent;
        this._super.apply(this, arguments);
        this._onFrequencyChange();
        this._onRecurringStartDateChange();
        this._resizeAmountInput();
        this._syncFeePercentFromSelection();
        this._updateCoverFeesSummary();
    },

    _onFrequencyChange: function () {
        const frequency = this.$('input[name="frequency"]:checked').val();
        const $startOptions = this.$('#recurring_start_options');
        const $startDate = this.$('#recurring_start_date');

        if (frequency && frequency !== 'one_time') {
            $startOptions.slideDown();
            $startDate.prop('required', true);
            const today = new Date();
            const iso = today.toISOString().split('T')[0];
            $startDate.attr('min', iso);
            if (!$startDate.val()) {
                $startDate.val(iso);
            }
            this._onRecurringStartDateChange();
        } else {
            $startOptions.slideUp();
            $startDate.prop('required', false);
        }
    },

    _onRecurringStartDateChange: function () {
        const $hint = this.$('#recurring_start_hint');
        if (!$hint.length) {
            return;
        }
        const startVal = this.$('#recurring_start_date').val();
        const today = new Date().toISOString().split('T')[0];
        if (!startVal || startVal <= today) {
            $hint.text(
                'Your donation is charged when you click Donate Now. The next charge follows your selected schedule.'
            );
        } else {
            $hint.text(
                'Donate Now saves your payment method only. Your first donation is charged on this date; later charges follow your schedule.'
            );
        }
    },

    _onAmountInput: function () {
        this._resizeAmountInput();
        this._updateCoverFeesSummary();
    },

    _onPaymentMethodChange: function () {
        this._syncFeePercentFromSelection();
        this._updateCoverFeesSummary();
    },

    _onEmailBlur: async function () {
        if (!this.isPublicUser) {
            return;
        }
        const email = (this.$('#email').val() || '').trim();
        const $guestBlock = this.$('#donation_saved_tokens_guest');
        const $list = this.$('#donation_saved_tokens_list');
        if (!email) {
            $guestBlock.hide();
            $list.empty();
            return;
        }
        try {
            const tokens = await rpc('/donate/tokens', { email });
            this._renderGuestTokens(tokens);
        } catch {
            $guestBlock.hide();
            $list.empty();
        }
    },

    _renderGuestTokens: function (tokens) {
        const $guestBlock = this.$('#donation_saved_tokens_guest');
        const $list = this.$('#donation_saved_tokens_list');
        $list.empty();
        if (!tokens || !tokens.length) {
            $guestBlock.hide();
            return;
        }
        const hasChecked = this.$('input[name="payment_method"]:checked').length > 0;
        tokens.forEach((token, index) => {
            const id = `payment_token_guest_${token.id}`;
            const checked = !hasChecked && index === 0 ? ' checked="checked"' : '';
            const feePercent = parseFloat(token.fee_percent);
            const label = document.createElement('label');
            label.className = 'donation-payment-option';
            label.setAttribute('for', id);
            label.innerHTML = `
                <input type="radio" class="donation-payment-option-input" name="payment_method"
                       id="${id}" value="token_${token.id}"
                       data-fee-percent="${feePercent}"${checked} required="required"/>
                <span class="donation-payment-option-body">
                    <span class="donation-payment-option-title">
                        <i class="fa fa-credit-card me-2 text-muted" aria-hidden="true"></i>
                        ${this._escapeHtml(token.display_name)}
                    </span>
                    <span class="donation-payment-option-meta">
                        ${this._escapeHtml(token.payment_method)} · ${this._escapeHtml(token.provider_name)}
                    </span>
                </span>
            `;
            $list.append(label);
        });
        $guestBlock.show();
        if (!hasChecked) {
            this._syncFeePercentFromSelection();
        }
    },

    _escapeHtml: function (text) {
        const el = document.createElement('div');
        el.textContent = text || '';
        return el.innerHTML;
    },

    _syncFeePercentFromSelection: function () {
        const $selected = this.$('input[name="payment_method"]:checked');
        let feePercent = this.defaultFeePercent;
        if ($selected.length) {
            const parsed = parseFloat($selected.data('feePercent'));
            if (Number.isFinite(parsed) && parsed >= 0) {
                feePercent = parsed;
            }
        }
        this.feePercent = feePercent;
        this.$('#fee_percent').val(feePercent);
        const label = Number.isInteger(feePercent) ? String(feePercent) : feePercent.toFixed(2);
        this.$('#cover_fees_percent_label').text(label);
    },

    _resizeAmountInput: function () {
        const input = this.el.querySelector('#amount');
        if (!input) {
            return;
        }
        const value = String(input.value || '').replace(/[^\d.]/g, '');
        const placeholder = String(input.getAttribute('placeholder') || '0');
        const length = Math.max((value || placeholder).length, 1);
        const widthCh = Math.min(Math.max(length + 0.5, 2.5), 12);
        input.style.width = `${widthCh}ch`;
    },

    _onCoverFeesChange: function () {
        this._updateCoverFeesSummary();
    },

    _getBaseAmount: function () {
        const raw = parseFloat(this.$('#amount').val());
        return Number.isFinite(raw) && raw > 0 ? raw : 0;
    },

    _updateCoverFeesSummary: function () {
        const base = this._getBaseAmount();
        const coverFees = this.$('#cover_fees').is(':checked');
        const $summary = this.$('#cover_fees_summary');

        if (!coverFees || base <= 0) {
            $summary.hide();
            return;
        }

        const fee = Math.round((base * this.feePercent / 100) * 100) / 100;
        const total = Math.round((base + fee) * 100) / 100;

        this.$('#cover_fees_amount').text(this._formatMoney(fee));
        this.$('#cover_fees_total').text(this._formatMoney(total));
        $summary.show();
    },

    _formatMoney: function (amount) {
        return '$' + amount.toFixed(2);
    },
});

export default publicWidget.registry.DonationForm;
