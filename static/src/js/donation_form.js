/** @odoo-module **/
// The /donate page: the gift (amount, fund, schedule, donor) on top, and
// Odoo's standard payment form below it, where each payment provider shows
// its own fields. "Donate Now" is the payment form's button; the gift's
// details go along with the payment (see the PaymentForm part below).
import publicWidget from "@web/legacy/js/public/public_widget";
import PaymentForm from "@payment/js/payment_form";

/**
 * The gift's fields on /donate (everything outside the payment form).
 *
 * @param {HTMLElement} donationEl - The .donation-form element.
 * @return {HTMLElement[]}
 */
function donationFields(donationEl) {
    return [...donationEl.querySelectorAll("input[name], select[name], textarea[name]")].filter(
        (el) => !el.closest("#o_payment_form")
    );
}

/** Check the gift's fields; show the first problem. */
export function checkDonationFields(donationEl) {
    for (const el of donationFields(donationEl)) {
        if (!el.checkValidity()) {
            el.reportValidity();
            return false;
        }
    }
    return true;
}

/** The gift's details, as the server reads them. */
export function donationValues(donationEl) {
    const values = {};
    for (const el of donationFields(donationEl)) {
        if ((el.type === "radio" || el.type === "checkbox") && !el.checked) {
            continue;
        }
        values[el.name] = el.type === "checkbox" ? "on" : el.value;
    }
    return values;
}

publicWidget.registry.DonationForm = publicWidget.Widget.extend({
    selector: ".donation-form",
    events: {
        "change input[name='frequency']": "_onFrequencyChange",
        "change #recurring_start_date": "_onRecurringStartDateChange",
        "input #donation_amount": "_onAmountInput",
        "change #cover_fees": "_onCoverFeesChange",
        "change input[name='o_payment_radio']": "_onPaymentOptionChange",
    },
    start: function () {
        this.defaultFeePercent = parseFloat(this.el.dataset.defaultFeePercent || "3.0");
        try {
            this.feePercents = JSON.parse(this.el.dataset.feePercents || "{}");
        } catch {
            this.feePercents = {};
        }
        this.feePercent = this.defaultFeePercent;
        const res = this._super.apply(this, arguments);
        this._onFrequencyChange();
        this._onRecurringStartDateChange();
        this._resizeAmountInput();
        this._syncFeePercentFromSelection();
        this._updateCoverFeesSummary();
        return res;
    },
    _onPaymentOptionChange: function () {
        this._syncFeePercentFromSelection();
        this._updateCoverFeesSummary();
    },
    // -------------------------------------------------------------------------
    // Donation form behaviour
    // -------------------------------------------------------------------------
    _onFrequencyChange: function () {
        const frequency = this.$("input[name='frequency']:checked").val();
        const $startOptions = this.$("#recurring_start_options");
        const $startDate = this.$("#recurring_start_date");
        if (frequency && frequency !== "one_time") {
            $startOptions.slideDown();
            $startDate.prop("required", true);
            const today = new Date();
            const iso = today.toISOString().split("T")[0];
            $startDate.attr("min", iso);
            if (!$startDate.val()) {
                $startDate.val(iso);
            }
            this._onRecurringStartDateChange();
        } else {
            $startOptions.slideUp();
            $startDate.prop("required", false);
        }
    },
    _onRecurringStartDateChange: function () {
        const $hint = this.$("#recurring_start_hint");
        if (!$hint.length) {
            return;
        }
        const startVal = this.$("#recurring_start_date").val();
        const today = new Date().toISOString().split("T")[0];
        if (!startVal || startVal <= today) {
            $hint.text(
                "Your donation is charged when you click Donate Now. The next charge follows your selected schedule."
            );
        } else {
            $hint.text(
                "Donate Now saves your payment method only. Your first donation is charged on this date; later charges follow your schedule."
            );
        }
    },
    _onAmountInput: function () {
        this._resizeAmountInput();
        this._updateCoverFeesSummary();
    },
    _syncFeePercentFromSelection: function () {
        const radio = this.el.querySelector('input[name="o_payment_radio"]:checked');
        let feePercent = this.defaultFeePercent;
        if (radio) {
            const key = `${radio.dataset.paymentOptionType}_${radio.dataset.paymentOptionId}`;
            const parsed = parseFloat(this.feePercents[key]);
            if (Number.isFinite(parsed) && parsed > 0) {
                feePercent = parsed;
            }
        }
        this.feePercent = feePercent;
        this.$("#fee_percent").val(feePercent);
        const label = Number.isInteger(feePercent) ? String(feePercent) : feePercent.toFixed(2);
        this.$("#cover_fees_percent_label").text(label);
    },
    _resizeAmountInput: function () {
        const input = this.el.querySelector("#donation_amount");
        if (!input) {
            return;
        }
        const value = String(input.value || "").replace(/[^\d.]/g, "");
        const placeholder = String(input.getAttribute("placeholder") || "0");
        const length = Math.max((value || placeholder).length, 1);
        const widthCh = Math.min(Math.max(length + 0.5, 2.5), 12);
        input.style.width = `${widthCh}ch`;
    },
    _onCoverFeesChange: function () {
        this._updateCoverFeesSummary();
    },
    _getBaseAmount: function () {
        const raw = parseFloat(this.$("#donation_amount").val());
        return Number.isFinite(raw) && raw > 0 ? raw : 0;
    },
    _updateCoverFeesSummary: function () {
        const base = this._getBaseAmount();
        const coverFees = this.$("#cover_fees").is(":checked");
        const $summary = this.$("#cover_fees_summary");
        if (!coverFees || base <= 0) {
            $summary.hide();
            return;
        }
        const fee = Math.round((base * this.feePercent / 100) * 100) / 100;
        const total = Math.round((base + fee) * 100) / 100;
        this.$("#cover_fees_amount").text(this._formatMoney(fee));
        this.$("#cover_fees_total").text(this._formatMoney(total));
        $summary.show();
    },
    _formatMoney: function (amount) {
        return "$" + amount.toFixed(2);
    },
});


PaymentForm.include({
    _getDonationEl() {
        return this.el.closest(".donation-form");
    },

    /**
     * Check the gift's details before any payment starts.
     *
     * @override method from @payment/js/payment_form
     */
    async _initiatePaymentFlow(providerCode, paymentOptionId, paymentMethodCode, flow) {
        const donationEl = this._getDonationEl();
        if (donationEl && !checkDonationFields(donationEl)) {
            this._enableButton();
            return;
        }
        await this._super(...arguments);
    },

    /**
     * Send the gift's details with the payment.
     *
     * @override method from @payment/js/payment_form
     */
    _prepareTransactionRouteParams() {
        const params = this._super(...arguments);
        const donationEl = this._getDonationEl();
        if (donationEl) {
            params.donation_values = donationValues(donationEl);
        }
        return params;
    },

    /**
     * A recurring gift on a saved card that starts later charges nothing
     * today: go straight to the thank-you page.
     *
     * @override method from @payment/js/payment_form
     */
    _processTokenFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (processingValues.donation_redirect) {
            window.location = processingValues.donation_redirect;
            return;
        }
        return this._super(...arguments);
    },
});

export default publicWidget.registry.DonationForm;
