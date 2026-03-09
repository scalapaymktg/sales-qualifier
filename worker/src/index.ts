interface Env {
  NGROK_URL: string;
  SLACK_BOT_TOKEN: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // Health check
    if (url.pathname === "/health") {
      return new Response(JSON.stringify({ status: "ok", proxy: true }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    // Solo POST su /slack/interactions
    if (url.pathname !== "/slack/interactions" || request.method !== "POST") {
      return new Response("Not found", { status: 404 });
    }

    // Leggi il body originale
    const body = await request.text();

    // Estrai channel_id dal payload Slack per eventuale notifica errore
    let channelId = "";
    try {
      const params = new URLSearchParams(body);
      const payloadStr = params.get("payload");
      if (payloadStr) {
        const payload = JSON.parse(payloadStr);
        channelId = payload?.channel?.id || "";
      }
    } catch {
      // Se non riusciamo a parsare, continuiamo comunque con il forward
    }

    // Forwarda al server ngrok
    const targetUrl = `${env.NGROK_URL}/slack/interactions`;

    try {
      const response = await fetch(targetUrl, {
        method: "POST",
        headers: {
          "Content-Type": request.headers.get("Content-Type") || "application/x-www-form-urlencoded",
        },
        body: body,
        signal: AbortSignal.timeout(5000), // 5 secondi timeout
      });

      // Se il server risponde, ritorna la risposta a Slack
      const responseBody = await response.text();
      return new Response(responseBody, {
        status: response.status,
        headers: { "Content-Type": response.headers.get("Content-Type") || "application/json" },
      });
    } catch (error) {
      // Server non raggiungibile - notifica su Slack
      console.error(`Forward failed: ${error}`);

      if (channelId && env.SLACK_BOT_TOKEN) {
        await notifySlackOffline(env.SLACK_BOT_TOKEN, channelId);
      }

      // Rispondi a Slack con 200 per evitare retry
      return new Response(JSON.stringify({
        response_action: "errors",
        errors: { general: "Server qualifier offline. Il team e' stato notificato." },
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
  },
};

async function notifySlackOffline(token: string, channelId: string): Promise<void> {
  try {
    await fetch("https://slack.com/api/chat.postMessage", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        channel: channelId,
        text: ":warning: *Server Sales Qualifier offline* — il bottone non ha funzionato. Contattare @stefano per riavviare il server.",
      }),
    });
  } catch (e) {
    console.error(`Failed to notify Slack: ${e}`);
  }
}
