/** @odoo-module **/
import publicWidget from "@web/legacy/js/public/public_widget";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
const HELCIM_JS_SCRIPT_URL = "https://secure.myhelcim.com/js/version2.js";
const HELCIM_PAY_SCRIPT_URL = "https://secure.helcim.app/helcim-pay/services/start.js";
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
        this._helcimJsScriptPromise = null;
        this._helcimPayScriptPromise = null;
        this._submitInProgress = false;
        this._paymentModal = null;
        this._super.apply(this, arguments);
        this._onFrequencyChange();
        this._onRecurringStartDateChange();
        this._resizeAmountInput();
        this._syncFeePercentFromSelection();
        this._updateCoverFeesSummary();
        this._updateHelcimPaymentFields();
        this._initPaymentPicker();
        if (this.el.dataset.helcimJsToken) {
            this._loadHelcimJsScript().catch(() => {});
        }
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
        if (!modalEl || !window.bootstrap?.Modal) {
            return null;
        }
        if (!this._paymentModal) {
            this._paymentModal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
        }
        return this._paymentModal;
    },
    _openPaymentModal: function () {
        this._updateHelcimPaymentFields();
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
        if (!this._isHelcimNewMethodSelected()) {
            return true;
        }
        if (this._getHelcimCheckoutMode() === "helcimpay_js") {
            return true;
        }
        const $fields = this.$("#o_donation_helcim_payment_details")
            .find("input, select")
            .filter(function () {
                return !this.closest(".d-none");
            });
        for (const el of $fields) {
            if (!el.checkValidity()) {
                if (report) {
                    el.reportValidity();
                }
                return false;
            }
        }
        return true;
    },
    _updatePaymentModalIntro: function () {
        const $intro = this.$("#o_donation_payment_modal_intro");
        const $selected = this._getSelectedPaymentInput();
        if (!$selected.length || !this._isNewPaymentMethod($selected)) {
            $intro.addClass("d-none").text("");
            return;
        }
        if (this._isHelcimNewMethodSelected()) {
            $intro.addClass("d-none").text("");
            const pmCode = $selected.data("paymentMethodCode") || "";
            const title =
                pmCode === "ach_direct_debit"
                    ? _t("Bank account details")
                    : _t("Card details");
            this.$("#o_donation_payment_modal_label").text(title);
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
        if (this._shouldUseHelcimPayJs()) {
            ev.preventDefault();
            await this._processHelcimPayDonation();
            return;
        }
        if (!this._shouldUseHelcimJs()) {
            return;
        }
        ev.preventDefault();
        if (this._submitInProgress) {
            return;
        }
        if (!this.el.reportValidity()) {
            return;
        }
        if (!this.el.dataset.helcimJsToken) {
            window.location = "/donate?error=helcim_js_not_configured";
            return;
        }
        this._submitInProgress = true;
        const $submit = this.$('button[type="submit"]');
        $submit.prop("disabled", true);
        try {
            await this._loadHelcimJsScript();
            if (typeof window.helcimProcess !== "function") {
                throw new Error(_t("Helcim.js did not load. Check your connection and try again."));
            }
            const payload = this._serializeForm();
            const result = await rpc("/donate/prepare_helcim_js", payload);
            if (result.error) {
                const msg = result.message || result.error;
                window.location = `/donate?error=${encodeURIComponent(msg)}`;
                return;
            }
            this.el.action = "/donate/helcim_js_complete";
            this.$("#o_donation_helcim_donation_id").val(result.donation_id);
            const helcimAmount =
                result.operation === "validation"
                    ? Number(result.amount).toFixed(2)
                    : Number(result.amount).toFixed(2);
            this.$("#amount").val(helcimAmount);
            this.$("#customerCode").val(result.customer_code || "");
            this.$("#test").val("0");
            if (typeof window.helcimProcess !== "function") {
                throw new Error(_t("Helcim.js is not ready. Please try again."));
            }
            window.helcimProcess();
        } catch (error) {
            const message =
                error.data?.message || error.message || _t("Could not start payment.");
            window.location = `/donate?error=${encodeURIComponent(message)}`;
        } finally {
            this._submitInProgress = false;
            $submit.prop("disabled", false);
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
    _shouldUseHelcimJs: function () {
        if (!this._isHelcimNewMethodSelected()) {
            return false;
        }
        const pmCode = this._getSelectedPaymentInput().data("paymentMethodCode");
        return (
            pmCode === "card" && this._getHelcimCheckoutMode() === "helcim_js"
        );
    },
    _shouldUseHelcimPayJs: function () {
        return (
            this._isHelcimNewMethodSelected()
            && this._getHelcimCheckoutMode() === "helcimpay_js"
        );
    },
    _processHelcimPayDonation: async function () {
        if (this._submitInProgress) {
            return;
        }
        if (!this.el.reportValidity()) {
            return;
        }
        this._submitInProgress = true;
        const $submit = this.$('button[type="submit"]');
        $submit.prop("disabled", true);
        try {
            const payload = this._serializeForm();
            const prepared = await rpc("/donate/prepare_helcim_pay", payload);
            if (prepared.error) {
                const msg = prepared.message || prepared.error;
                window.location = `/donate?error=${encodeURIComponent(msg)}`;
                return;
            }
            await this._runHelcimPayCheckout({
                reference: prepared.reference,
                paymentMethodCode: prepared.payment_method_code,
                tokenize: prepared.tokenize,
                donationId: prepared.donation_id,
                accessToken: prepared.access_token,
            });
        } catch (error) {
            const message =
                error.data?.message || error.message || _t("Could not start payment.");
            window.location = `/donate?error=${encodeURIComponent(message)}`;
        } finally {
            this._submitInProgress = false;
            $submit.prop("disabled", false);
        }
    },
    _runHelcimPayCheckout: async function (options) {
        const init = await rpc("/payment/helcim/initialize_checkout", {
            reference: options.reference,
            payment_method: options.paymentMethodCode,
            tokenization_requested: options.tokenize,
        });
        if (!init || !init.success) {
            throw new Error(init?.error_message || _t("Payment initialization failed."));
        }
        await this._loadHelcimPayScript();
        if (typeof window.appendHelcimPayIframe !== "function") {
            throw new Error(_t("HelcimPay.js did not load. Please try again."));
        }
        await window.appendHelcimPayIframe(init.checkoutToken, true);
        const checkoutToken = init.checkoutToken;
        const handler = async (event) => {
            const eventKey = "helcim-pay-js-" + checkoutToken;
            if (event.data?.eventName !== eventKey) {
                return;
            }
            if (event.data.eventStatus === "SUCCESS") {
                window.removeEventListener("message", handler);
                try {
                    const status = await rpc("/payment/helcim/process_payment_status", {
                        reference: options.reference,
                        event_status: event.data.eventStatus,
                        event_message: event.data.eventMessage,
                        tokenization_requested: options.tokenize,
                    });
                    if (status?.status !== "success") {
                        throw new Error(status?.message || _t("Payment was declined."));
                    }
                    const donationId = status.donation_id || options.donationId;
                    const accessToken = encodeURIComponent(
                        status.access_token || options.accessToken || ""
                    );
                    window.location = `/donate/confirmation/${donationId}?access_token=${accessToken}`;
                } catch (error) {
                    console.error("HelcimPay donation status error:", error);
                    const message =
                        error.data?.message || error.message || _t("Payment failed.");
                    window.location = `/donate?error=${encodeURIComponent(message)}`;
                }
                return;
            }
            if (event.data.eventStatus === "HIDE") {
                window.removeEventListener("message", handler);
            }
        };
        window.addEventListener("message", handler);
    },
    _loadHelcimPayScript: function () {
        if (typeof window.appendHelcimPayIframe === "function") {
            return Promise.resolve();
        }
        if (this._helcimPayScriptPromise) {
            return this._helcimPayScriptPromise;
        }
        this._helcimPayScriptPromise = new Promise((resolve, reject) => {
            const existing = document.querySelector(`script[src="${HELCIM_PAY_SCRIPT_URL}"]`);
            if (existing) {
                existing.addEventListener("load", () => resolve());
                existing.addEventListener("error", () =>
                    reject(new Error("HelcimPay.js failed to load."))
                );
                return;
            }
            const script = document.createElement("script");
            script.type = "text/javascript";
            script.src = HELCIM_PAY_SCRIPT_URL;
            script.onload = () => resolve();
            script.onerror = () => {
                this._helcimPayScriptPromise = null;
                reject(new Error("HelcimPay.js failed to load."));
            };
            document.head.appendChild(script);
        });
        return this._helcimPayScriptPromise;
    },
    _getHelcimCheckoutMode: function () {
        const $selected = this._getSelectedPaymentInput();
        if ($selected.length && $selected.data("helcimCheckoutMode")) {
            return $selected.data("helcimCheckoutMode");
        }
        return this.el.dataset.helcimCheckoutMode || "helcim_js";
    },
    _loadHelcimJsScript: function () {
        if (typeof window.helcimProcess === "function") {
            return Promise.resolve();
        }
        if (this._helcimJsScriptPromise) {
            return this._helcimJsScriptPromise;
        }
        this._helcimJsScriptPromise = new Promise((resolve, reject) => {
            const existing = document.querySelector(`script[src="${HELCIM_JS_SCRIPT_URL}"]`);
            if (existing) {
                existing.addEventListener("load", () => resolve());
                existing.addEventListener("error", () => reject(new Error("Helcim.js failed to load.")));
                return;
            }
            const script = document.createElement("script");
            script.type = "text/javascript";
            script.src = HELCIM_JS_SCRIPT_URL;
            script.onload = () => resolve();
            script.onerror = () => {
                this._helcimJsScriptPromise = null;
                reject(new Error("Helcim.js failed to load."));
            };
            document.head.appendChild(script);
        });
        return this._helcimJsScriptPromise;
    },
    // -------------------------------------------------------------------------
    // Helcim payment fields visibility
    // -------------------------------------------------------------------------
    _onPaymentMethodChange: function () {
        this._syncFeePercentFromSelection();
        this._updateCoverFeesSummary();
        this._updateHelcimPaymentFields();
        this._updatePaymentPickerDisplay();
        if (this._shouldUseHelcimJs()) {
            this._loadHelcimJsScript().catch(() => {});
        }
    },
    _getSelectedPaymentInput: function () {
        return this.$("input[name='payment_method']:checked");
    },
    _isHelcimNewMethodSelected: function () {
        const $selected = this._getSelectedPaymentInput();
        if (!$selected.length) {
            return false;
        }
        const value = String($selected.val() || "");
        return $selected.data("providerCode") === "helcim" && value.startsWith("method_");
    },
    _isRecurringDonation: function () {
        const frequency = this.$("input[name='frequency']:checked").val();
        return frequency && frequency !== "one_time";
    },
    _setHelcimFieldRequired: function ($container, required) {
        $container.find("input, select").each(function () {
            if (required) {
                this.setAttribute("required", "required");
            } else {
                this.removeAttribute("required");
            }
        });
    },
    _updateHelcimPaymentFields: function () {
        const $details = this.$("#o_donation_helcim_payment_details");
        const $cardJs = this.$("#o_donation_helcim_card_js");
        const $cardApi = this.$("#o_donation_helcim_card_api");
        const $ach = this.$("#o_donation_helcim_ach");
        const $save = this.$("#o_donation_helcim_save");
        if (!this._isHelcimNewMethodSelected()) {
            $details.addClass("d-none");
            $cardJs.addClass("d-none");
            $cardApi.addClass("d-none");
            $ach.addClass("d-none");
            $save.addClass("d-none");
            this._setHelcimFieldRequired($cardJs, false);
            this._setHelcimFieldRequired($cardApi, false);
            this._setHelcimFieldRequired($ach, false);
            return;
        }
        const pmCode = this._getSelectedPaymentInput().data("paymentMethodCode") || "";
        const mode = this._getHelcimCheckoutMode();
        $details.removeClass("d-none");
        if (mode === "helcimpay_js") {
            $cardJs.addClass("d-none");
            $cardApi.addClass("d-none");
            $ach.addClass("d-none");
            $save.addClass("d-none");
            this._setHelcimFieldRequired($cardJs, false);
            this._setHelcimFieldRequired($cardApi, false);
            this._setHelcimFieldRequired($ach, false);
            return;
        }
        const showCard = pmCode === "card";
        const showAch = pmCode === "ach_direct_debit";
        const showCardJs = showCard && mode === "helcim_js";
        const showCardApi = showCard && mode === "direct_api";
        $cardJs.toggleClass("d-none", !showCardJs);
        $cardApi.toggleClass("d-none", !showCardApi);
        $ach.toggleClass("d-none", !showAch);
        this._setHelcimFieldRequired($cardJs, showCardJs);
        this._setHelcimFieldRequired($cardApi, showCardApi);
        this._setHelcimFieldRequired($ach, showAch);
        if (showAch) {
            const isCanada =
                (this.el.dataset.partnerCountry || "").toUpperCase() === "CA";
            this.$(".o_donation_helcim_ach_us").toggleClass("d-none", isCanada);
            this.$(".o_donation_helcim_ach_ca").toggleClass("d-none", !isCanada);
            this.$("#helcim_bank_routing").prop("required", !isCanada);
            this.$("#helcim_bank_financial, #helcim_bank_transit").prop(
                "required",
                isCanada
            );
        }
        const recurring = this._isRecurringDonation();
        if (recurring) {
            $save.addClass("d-none");
            this.$("#helcim_save_payment_method").prop("checked", true);
        } else {
            $save.toggleClass("d-none", !showCard);
        }
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
        this._updateHelcimPaymentFields();
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
        try {
            const tokens = await rpc("/donate/tokens", { email });
            this._renderGuestTokens(tokens);
        } catch {
            $guestHeader.addClass("d-none");
            this._clearGuestTokenMenuItems();
            $guestRadios.empty();
        }
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

