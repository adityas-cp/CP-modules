import { registry } from "@web/core/registry";

/**
 * Opens the AI chat linked to a procurement request. The ai.composer of the
 * module routes "chatter_ai_button" chats on procurement.request to the
 * Procurement Advisor agent.
 */
async function openProcurementAIChat(env, action) {
    const { res_model, res_id, channel_title, user_message } = action.params || {};
    await env.services.aiChatLauncher.launchAIChat({
        interfaceKey: "chatter_ai_button",
        recordModel: res_model,
        recordId: res_id,
        channelTitle: channel_title,
        userMessage: user_message,
    });
}

registry.category("actions").add("cp_procurement_open_ai_chat", openProcurementAIChat);
