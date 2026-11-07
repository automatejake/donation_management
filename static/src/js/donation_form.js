/** @odoo-module **/
import publicWidget from "@web/legacy/js/public/public_widget";
import { _t } from "@web/core/l10n/translation";
const PICKER_ICON_CLASSES = {
    "credit-card": "fa-credit-card",
    university: "fa-university",
    bank: "fa-bank",
};
publicWidget.registry.DonationForm = publicWidget.Widget.extend({
    selector: ".donation-form",
    events: {
        "change input[name='frequency']": "_onFrequencyChange",
        "change #recurring_start_date": "_onRecurringStartDateChange",
        "input #donation_amount": "_onAmountInput",
        "change #cover_fees": "_onCoverFeesChange",
        "change input[name='payment_method']": "_onPaymentMethodChange",
        "click .donation-payment-picker-item": "_onPaymentPickerItemClick",
        "click #donation_payment_edit_btn": "_openPaymentModal",
        "click #o_donation_payment_modal_done": "_onPaymentModalDone",
        "blur #email": "_onEmailBlur",
        submit: "_onSubmit",
    },
    start: function () {
        this.defaultFeePercent = parseFloat(this.el.dataset.defaultFeePercent || "3.0");
        this.isPublicUser = this.el.dataset.isPublic === "1";
        this.feePercent = this.defaultFeePercent;
        this._submitInProgress = false;
        this._paymentModal = null;
        this._super.apply(this, arguments);
        this._onFrequencyChange();
        this._onRecurringStartDateChange();
        this._resizeAmountInput();
        this._syncFeePercentFromSelection();
        this._updateCoverFeesSummary();
        this._updateProviderPaymentFields();
        this._initPaymentPicker();
    },
    // -------------------------------------------------------------------------
    // Payment method dropdown + modal
    // -------------------------------------------------------------------------
    _initPaymentPicker: function () {
        this._updatePaymentPickerDisplay();
    },
    _onPaymentPickerItemClick: function (ev) {
        ev.preventDefault();
        const radioId = ev.currentTarget.dataset.radioId;
        if (radioId) {
            this._selectPaymentMethodByRadioId(radioId);
        }
    },
    _selectPaymentMethodByRadioId: function (radioId) {
        const $radio = this.$(`#${radioId}`);
        if (!$radio.length) {
            return;
        }
        $radio.prop("checked", true).trigger("change");
        this._updatePaymentPickerDisplay();
        if (this._isNewPaymentMethod($radio)) {
            this._openPaymentModal();
        } else {
            this._closePaymentModal();
            this.$("#donation_payment_edit_btn").addClass("d-none");
        }
    },
    _isNewPaymentMethod: function ($input) {
        if (!$input || !$input.length) {
            return false;
        }
        if ($input.data("pickerIsNew")) {
            return true;
        }
        const value = String($input.val() || "");
        return value.startsWith("method_") || value.startsWith("provider_");
    },
    _getPaymentModal: function () {
        const modalEl = document.getElementById("o_donation_payment_modal");
        // Odoo 18's website loads Bootstrap's Modal as window.Modal; there is
        // no window.bootstrap, so the payment details window never opened.
        const Modal = window.bootstrap?.Modal || window.Modal;
        if (!modalEl || !Modal) {
            return null;
        }
        if (!this._paymentModal) {
            this._paymentModal = Modal.getOrCreateInstance(modalEl);
        }
        return this._paymentModal;
    },
    _openPaymentModal: function () {
        this._updateProviderPaymentFields();
        this._updatePaymentModalIntro();
        const modal = this._getPaymentModal();
        if (modal) {
            modal.show();
        }
        this.$("#donation_payment_edit_btn").removeClass("d-none");
    },
    _closePaymentModal: function () {
        const modal = this._getPaymentModal();
        if (modal) {
            modal.hide();
        }
    },
    _onPaymentModalDone: function () {
        if (!this._validatePaymentModalFields(true)) {
            return;
        }
        this._closePaymentModal();
    },
    _validatePaymentModalFields: function (report) {
        const $selected = this._getSelectedPaymentInput();
        if (!$selected.length || !this._isNewPaymentMethod($selected)) {
            return true;
        }
        return this._validateProviderPaymentFields(report);
    },
    _updatePaymentModalIntro: function () {
        const $intro = this.$("#o_donation_payment_modal_intro");
        const $selected = this._getSelectedPaymentInput();
        if (!$selected.length || !this._isNewPaymentMethod($selected)) {
            $intro.addClass("d-none").text("");
            return;
        }
        if (this._updateProviderModalIntro($selected)) {
            return;
        }
        const methodName = $selected.data("pickerTitle") || _t("this method");
        $intro
            .removeClass("d-none")
            .text(
                _t(
                    "You selected %(method)s. Click Done to continue, then Donate Now to complete your gift.",
                    { method: methodName }
                )
            );
        this.$("#o_donation_payment_modal_label").text(_t("Payment method"));
    },
    _updatePaymentPickerDisplay: function () {
        const $selected = this._getSelectedPaymentInput();
        const $title = this.$(".donation-payment-dropdown-title");
        const $meta = this.$(".donation-payment-dropdown-meta");
        const $icon = this.$(".donation-payment-dropdown-icon");
        if (!$selected.length) {
            $title.text(_t("Select payment method"));
            $meta.text("");
            $icon.attr("class", "fa fa-credit-card donation-payment-dropdown-icon");
            return;
        }
        const title = $selected.data("pickerTitle") || $selected.val();
        const meta = $selected.data("pickerMeta") || "";
        const iconKey = $selected.data("pickerIcon") || "credit-card";
        const iconClass = PICKER_ICON_CLASSES[iconKey] || PICKER_ICON_CLASSES["credit-card"];
        $title.text(title);
        $meta.text(meta);
        $icon.attr("class", `fa ${iconClass} donation-payment-dropdown-icon`);
        this.$(".donation-payment-picker-item").removeClass("active");
        const radioId = $selected.attr("id");
        if (radioId) {
            this.$(`.donation-payment-picker-item[data-radio-id="${radioId}"]`).addClass(
                "active"
            );
        }
        if (this._isNewPaymentMethod($selected)) {
            this.$("#donation_payment_edit_btn").removeClass("d-none");
        } else {
            this.$("#donation_payment_edit_btn").addClass("d-none");
        }
    },
    // -------------------------------------------------------------------------
    // Form submit
    // -------------------------------------------------------------------------
    _onSubmit: async function (ev) {
        const $selected = this._getSelectedPaymentInput();
        if (
            $selected.length &&
            this._isNewPaymentMethod($selected) &&
            !this._validatePaymentModalFields(true)
        ) {
            ev.preventDefault();
            this._openPaymentModal();
            return;
        }
        const providerSubmit = this._handleProviderSubmit(ev);
        if (providerSubmit) {
            await providerSubmit;
        }
    },
    _serializeForm: function () {
        const data = {};
        const formData = new FormData(this.el);
        for (const [key, value] of formData.entries()) {
            if (key === "cover_fees") {
                data[key] = "on";
            } else {
                data[key] = value;
            }
        }
        if (!this.$("#cover_fees").is(":checked")) {
            delete data.cover_fees;
        }
        return data;
    },
    // -------------------------------------------------------------------------
    // Payment provider hooks: a provider's bridge module overrides these to
    // add its own fields and checkout.
    // -------------------------------------------------------------------------
    /** Show or hide the provider's own payment fields for the selected method. */
    _updateProviderPaymentFields: function () {},
    /** Check the provider's own fields; return false to keep the modal open. */
    _validateProviderPaymentFields: function (report) {
        return true;
    },
    /** Set the payment modal's title and intro; return true when handled. */
    _updateProviderModalIntro: function ($selected) {
        return false;
    },
    /**
     * Take over the submit for the selected method: call ev.preventDefault()
     * before any await and return a promise, or return false to let the form
     * post normally.
     */
    _handleProviderSubmit: function (ev) {
        return false;
    },
    _onPaymentMethodChange: function () {
        this._syncFeePercentFromSelection();
        this._updateCoverFeesSummary();
        this._updateProviderPaymentFields();
        this._updatePaymentPickerDisplay();
    },
    _getSelectedPaymentInput: function () {
        return this.$("input[name='payment_method']:checked");
    },
    _isRecurringDonation: function () {
        const frequency = this.$("input[name='frequency']:checked").val();
        return frequency && frequency !== "one_time";
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
        this._updateProviderPaymentFields();
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
    _onEmailBlur: async function () {
        if (!this.isPublicUser) {
            return;
        }
        const email = (this.$("#email").val() || "").trim();
        const $guestHeader = this.$("#donation_saved_tokens_guest_header");
        const $guestRadios = this.$("#donation_saved_tokens_guest_radios");
        if (!email) {
            $guestHeader.addClass("d-none");
            this._clearGuestTokenMenuItems();
            $guestRadios.empty();
            return;
        }
        // Saved payment methods are only offered to signed-in donors.
        $guestHeader.addClass("d-none");
        this._clearGuestTokenMenuItems();
        $guestRadios.empty();
    },

    _clearGuestTokenMenuItems: function () {
        this.$("#donation_payment_dropdown_menu .donation-payment-picker-item--guest").closest("li").remove();
    },

    _renderGuestTokens: function (tokens) {
        const $guestHeader = this.$("#donation_saved_tokens_guest_header");
        const $guestRadios = this.$("#donation_saved_tokens_guest_radios");
        this._clearGuestTokenMenuItems();
        $guestRadios.empty();
        if (!tokens || !tokens.length) {
            $guestHeader.addClass("d-none");
            return;
        }
        const hasChecked = this.$("input[name='payment_method']:checked").length > 0;
        let menuAnchor = $guestHeader[0];
        tokens.forEach((token, index) => {
            const radioId = `payment_token_guest_${token.id}`;
            const checked = !hasChecked && index === 0;
            const feePercent = parseFloat(token.fee_percent);
            const meta = `${token.payment_method} · ${token.provider_name}`;
            const radio = document.createElement("input");
            radio.type = "radio";
            radio.className = "donation-payment-option-input";
            radio.name = "payment_method";
            radio.id = radioId;
            radio.value = `token_${token.id}`;
            radio.required = true;
            radio.dataset.feePercent = String(feePercent);
            radio.dataset.pickerTitle = token.display_name;
            radio.dataset.pickerMeta = meta;
            radio.dataset.pickerIcon = "credit-card";
            if (checked) {
                radio.checked = true;
            }
            $guestRadios[0].appendChild(radio);

            const li = document.createElement("li");
            li.innerHTML = `
                <button type="button" class="dropdown-item donation-payment-picker-item"
                        data-radio-id="${radioId}">
                    <span class="donation-payment-picker-item-icon">
                        <i class="fa fa-credit-card text-muted" aria-hidden="true"></i>
                    </span>
                    <span class="donation-payment-picker-item-body">
                        <span class="donation-payment-picker-item-title">${this._escapeHtml(token.display_name)}</span>
                        <span class="donation-payment-picker-item-meta">${this._escapeHtml(meta)}</span>
                    </span>
                </button>
            `;
            menuAnchor.after(li);
            menuAnchor = li;
        });
        $guestHeader.removeClass("d-none");
        if (!hasChecked) {
            this._onPaymentMethodChange();
        } else {
            this._updatePaymentPickerDisplay();
        }
    },
    _escapeHtml: function (text) {
        const el = document.createElement("div");
        el.textContent = text || "";
        return el.innerHTML;
    },
    _syncFeePercentFromSelection: function () {
        const $selected = this._getSelectedPaymentInput();
        let feePercent = this.defaultFeePercent;
        if ($selected.length) {
            const parsed = parseFloat($selected.data("feePercent"));
            if (Number.isFinite(parsed) && parsed >= 0) {
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
export default publicWidget.registry.DonationForm;

