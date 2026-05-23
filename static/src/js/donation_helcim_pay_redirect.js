/** @odoo-module **/

import { rpc } from "@web/core/network/rpc";

const HELCIM_PAY_SCRIPT_URL = "https://secure.helcim.app/helcim-pay/services/start.js";

function loadHelcimPayScript() {
    if (typeof window.appendHelcimPayIframe === "function") {
        return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
        const existing = document.querySelector(`script[src="${HELCIM_PAY_SCRIPT_URL}"]`);
        if (existing) {
            existing.addEventListener("load", () => resolve());
            existing.addEventListener("error", () => reject(new Error("HelcimPay.js failed to load.")));
            return;
        }
        const script = document.createElement("script");
        script.type = "text/javascript";
        script.src = HELCIM_PAY_SCRIPT_URL;
        script.onload = () => resolve();
        script.onerror = () => reject(new Error("HelcimPay.js failed to load."));
        document.head.appendChild(script);
    });
}

async function runHelcimPayCheckout(options) {
    const init = await rpc("/payment/helcim/initialize_checkout", {
        reference: options.reference,
        payment_method: options.paymentMethodCode,
        tokenization_requested: options.tokenize,
    });
    if (!init || !init.success) {
        throw new Error(init?.error_message || "Payment initialization failed.");
    }
    await loadHelcimPayScript();
    if (typeof window.appendHelcimPayIframe !== "function") {
        throw new Error("HelcimPay.js did not load.");
    }
    await window.appendHelcimPayIframe(init.checkoutToken, true);
    const checkoutToken = init.checkoutToken;
    window.addEventListener("message", async (event) => {
        const eventKey = "helcim-pay-js-" + checkoutToken;
        if (event.data?.eventName !== eventKey) {
            return;
        }
        if (event.data.eventStatus === "SUCCESS") {
            try {
                const status = await rpc("/payment/helcim/process_payment_status", {
                    reference: options.reference,
                    event_status: event.data.eventStatus,
                    event_message: event.data.eventMessage,
                    tokenization_requested: options.tokenize,
                });
                if (status?.status !== "success") {
                    throw new Error(status?.message || "Payment was declined.");
                }
                const donationId = status.donation_id || options.donationId;
                const token = encodeURIComponent(
                    status.access_token || options.accessToken || ""
                );
                window.location = `/donate/confirmation/${donationId}?access_token=${token}`;
            } catch (error) {
                console.error("HelcimPay donation status error:", error);
                window.location = `/donate?error=${encodeURIComponent(error.message || "payment_failed")}`;
            }
        }
    });
}

document.addEventListener("DOMContentLoaded", () => {
    const bootstrap = document.getElementById("o_donation_helcim_pay_bootstrap");
    if (!bootstrap) {
        return;
    }
    const options = {
        reference: bootstrap.dataset.reference,
        paymentMethodCode: bootstrap.dataset.paymentMethodCode,
        tokenize: bootstrap.dataset.tokenize === "1",
        donationId: bootstrap.dataset.donationId,
        accessToken: bootstrap.dataset.accessToken,
    };
    runHelcimPayCheckout(options).catch((error) => {
        console.error(error);
        window.location = `/donate?error=${encodeURIComponent(error.message || "payment_failed")}`;
    });
});
