/* Freegie Dashboard */

var ws = null;
var reconnectTimer = null;
var sliderDragging = false;
var daemonStopped = false;

// --- Charge Range Slider (noUiSlider) ---

var chargeSlider = document.getElementById("charge-slider");

noUiSlider.create(chargeSlider, {
    start: [75, 80],
    connect: true,
    margin: 1,
    step: 1,
    range: { min: 20, max: 100 },
    format: {
        to: function(v) { return Math.round(v); },
        from: function(v) { return Number(v); }
    }
});

chargeSlider.noUiSlider.on("update", function(values) {
    document.getElementById("min-label").textContent = values[0] + "%";
    document.getElementById("max-label").textContent = values[1] + "%";
});

chargeSlider.noUiSlider.on("start", function() {
    sliderDragging = true;
});

chargeSlider.noUiSlider.on("change", function(values) {
    sliderDragging = false;
    putSettings({ charge_min: values[0], charge_max: values[1] });
});

chargeSlider.noUiSlider.on("end", function() {
    sliderDragging = false;
});

// --- WebSocket ---

function connectWS() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(proto + "//" + location.host + "/ws");

    ws.onmessage = function(evt) {
        var msg = JSON.parse(evt.data);
        if (msg.type === "status_update") {
            applyStatus(msg.data);
        }
    };

    ws.onclose = function() {
        ws = null;
        if (daemonStopped) {
            showDaemonStopped();
            return;
        }
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(connectWS, 2000);
    };

    ws.onerror = function() {
        ws.close();
    };
}

function sendAction(type, payload) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(Object.assign({ type: type }, payload || {})));
    }
}

// --- Status Application ---

function applyStatus(s) {
    updateBattery(s);
    updatePhase(s);
    updateTelemetry(s);
    updateDevice(s);
    updateControls(s);
}

// --- DOM Updaters ---

var RING_CIRCUMFERENCE = 2 * Math.PI * 85; // matches r=85 in SVG

function updateBattery(s) {
    var pct = s.battery_percent;
    var el = document.getElementById("battery-percent");
    var ring = document.getElementById("ring-fill");

    if (pct === null || pct === undefined) {
        el.textContent = "--";
        ring.style.strokeDashoffset = RING_CIRCUMFERENCE;
        return;
    }

    el.textContent = pct + "%";
    var offset = RING_CIRCUMFERENCE * (1 - pct / 100);
    ring.style.strokeDashoffset = offset;

    if (pct <= 20) {
        ring.style.stroke = "var(--danger)";
    } else if (pct <= 40) {
        ring.style.stroke = "var(--warning)";
    } else {
        ring.style.stroke = "var(--primary)";
    }
}

function showDaemonStopped() {
    var badge = document.getElementById("phase-badge");
    badge.textContent = "stopped";
    badge.className = "phase-badge disconnected";
    var dot = document.getElementById("status-dot");
    dot.className = "status-dot";
    var text = document.getElementById("connection-text");
    text.textContent = "Daemon Stopped";
}

function updatePhase(s) {
    if (daemonStopped) {
        showDaemonStopped();
        return;
    }

    var badge = document.getElementById("phase-badge");
    var phase = s.phase || "idle";
    badge.textContent = phase;
    badge.className = "phase-badge " + phase;

    var dot = document.getElementById("status-dot");
    var text = document.getElementById("connection-text");

    var dotClass = "";
    var label = phase.charAt(0).toUpperCase() + phase.slice(1);

    if (phase === "controlling" || phase === "paused") {
        dotClass = "connected";
        if (s.override === "on") {
            label = "Force On";
        } else if (s.override === "off") {
            label = "Force Off";
        }
    } else if (phase === "scanning" || phase === "connecting" || phase === "verifying") {
        dotClass = "active";
    } else if (phase === "reconnecting") {
        dotClass = "warning";
        var attempt = s.reconnect_attempt || 0;
        var delay = s.reconnect_delay || 0;
        label = "Reconnecting (attempt " + attempt + ", " + delay + "s)";
    } else if (phase === "disconnected") {
        dotClass = "error";
    }

    dot.className = "status-dot" + (dotClass ? " " + dotClass : "");
    text.textContent = label;
}

function updateTelemetry(s) {
    var card = document.getElementById("telemetry-card");
    if (!s.telemetry) {
        card.style.display = "none";
        return;
    }
    card.style.display = "";
    document.getElementById("metric-volts").textContent = s.telemetry.volts.toFixed(2);
    document.getElementById("metric-amps").textContent = s.telemetry.amps.toFixed(2);
    document.getElementById("metric-watts").textContent = s.telemetry.watts.toFixed(2);
}

function updateDevice(s) {
    var card = document.getElementById("device-card");
    if (!s.device) {
        card.style.display = "none";
        return;
    }
    card.style.display = "";
    document.getElementById("device-name").textContent = s.device.name || "--";
    document.getElementById("device-fw").textContent = s.device.firmware || "--";
    document.getElementById("device-hw").textContent = s.device.hardware || "--";
    var caps = s.device.capabilities;
    document.getElementById("device-pd").textContent = caps ? (caps.pd ? "Yes" : "No") : "--";
}

function updateControls(s) {
    // Charge range slider — only push values if user is not dragging
    if (!sliderDragging) {
        chargeSlider.noUiSlider.set([s.charge_min, s.charge_max]);
    }

    // Telemetry interval
    document.getElementById("telem-value").textContent = s.telemetry_interval;

    // PD mode
    var mode1 = document.getElementById("pd-mode-1");
    var mode2 = document.getElementById("pd-mode-2");
    if (s.pd_mode === 1) {
        mode1.classList.add("active");
        mode2.classList.remove("active");
    } else {
        mode1.classList.remove("active");
        mode2.classList.add("active");
    }

    // Override row
    var ovrRow = document.getElementById("override-row");
    var isConnected = (s.phase === "controlling" || s.phase === "paused");
    ovrRow.style.display = isConnected ? "" : "none";

    var ovrOn = document.getElementById("ovr-on");
    var ovrAuto = document.getElementById("ovr-auto");
    var ovrOff = document.getElementById("ovr-off");

    ovrOn.className = "mode-btn" + (s.override === "on" ? " override-on" : "");
    ovrAuto.className = "mode-btn" + (s.override === null ? " active" : "");
    ovrOff.className = "mode-btn" + (s.override === "off" ? " override-off" : "");
}

// --- Action Handlers ---

function putSettings(data) {
    fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
    });
}

function postAction(endpoint) {
    fetch("/api/" + endpoint, { method: "POST" });
}

function postActionJSON(endpoint, data) {
    fetch("/api/" + endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
    });
}

// Scan / Disconnect
document.getElementById("btn-scan").addEventListener("click", function() {
    postAction("scan");
});

document.getElementById("btn-disconnect").addEventListener("click", function() {
    postAction("disconnect");
});

document.getElementById("btn-stop").addEventListener("click", function() {
    daemonStopped = true;
    postAction("shutdown");
});

// Manual telemetry refresh
document.getElementById("btn-poll").addEventListener("click", function() {
    postAction("poll");
});

// Telemetry interval +/-
document.getElementById("telem-minus").addEventListener("click", function() {
    var cur = parseInt(document.getElementById("telem-value").textContent);
    var v = Math.max(5, cur - 5);
    putSettings({ telemetry_interval: v });
});

document.getElementById("telem-plus").addEventListener("click", function() {
    var cur = parseInt(document.getElementById("telem-value").textContent);
    var v = Math.min(300, cur + 5);
    putSettings({ telemetry_interval: v });
});

// PD mode
document.getElementById("pd-mode-1").addEventListener("click", function() {
    putSettings({ pd_mode: 1 });
});

document.getElementById("pd-mode-2").addEventListener("click", function() {
    putSettings({ pd_mode: 2 });
});

// Override buttons
document.getElementById("ovr-on").addEventListener("click", function() {
    postActionJSON("override", { mode: "on" });
});

document.getElementById("ovr-auto").addEventListener("click", function() {
    postActionJSON("override", { mode: "auto" });
});

document.getElementById("ovr-off").addEventListener("click", function() {
    postActionJSON("override", { mode: "off" });
});

// --- Init ---
connectWS();
