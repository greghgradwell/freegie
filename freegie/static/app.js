/* Freegie Dashboard */

var ws = null;
var reconnectTimer = null;
var sliderDragging = false;
var daemonStopped = false;

var CHART_BUFFER_SIZE = 2400;
var chartData = [[], [], [], [], []];
var chart = null;
var lastChargingState = null;
var lastChartPercent = null;

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
    updateChartThresholds(values[1], values[0]);
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
        } else if (msg.type === "chart_history") {
            loadChartHistory(msg.data);
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
    pushChartPoint(s);
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
    badge.textContent = phase.replace(/_/g, " ");
    badge.className = "phase-badge " + phase;

    var dot = document.getElementById("status-dot");
    var text = document.getElementById("connection-text");

    var dotClass = "";
    var label = phase.charAt(0).toUpperCase() + phase.slice(1);

    if (phase === "charging" || phase === "paused") {
        dotClass = "connected";
        if (s.override === "on") {
            label = "Force On";
        } else if (s.override === "off") {
            label = "Force Off";
        }
    } else if (phase === "negotiating_charge") {
        dotClass = "active";
        label = "Negotiating Charge";
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
    var isConnected = (s.phase === "charging" || s.phase === "paused" || s.phase === "negotiating_charge");
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

// --- Chart ---

function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function initChart() {
    if (typeof uPlot === "undefined") return;

    var container = document.getElementById("chart-container");
    var axisStroke = cssVar("--text-secondary");
    var gridStroke = "rgba(255,255,255,0.06)";
    var chargingColor = cssVar("--primary");
    var pausedColor = cssVar("--warning");
    var thresholdColor = cssVar("--text-secondary");

    var opts = {
        width: container.clientWidth,
        height: 200,
        cursor: { show: false },
        select: { show: false },
        legend: { show: false },
        axes: [
            {
                stroke: axisStroke,
                grid: { stroke: gridStroke, width: 1 },
                ticks: { stroke: gridStroke, width: 1 },
                incrs: [60, 120, 300, 600, 900, 1800, 3600],
                values: function(self, ticks) {
                    return ticks.map(function(v) {
                        var d = new Date(v * 1000);
                        var h = d.getHours();
                        var m = d.getMinutes();
                        return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
                    });
                }
            },
            {
                stroke: axisStroke,
                grid: { stroke: gridStroke, width: 1 },
                ticks: { stroke: gridStroke, width: 1 },
                values: function(self, ticks) {
                    return ticks.map(function(v) { return v + "%"; });
                },
                range: [0, 100]
            }
        ],
        series: [
            {},
            {
                label: "Charging",
                stroke: chargingColor,
                width: 2,
                paths: uPlot.paths.spline(),
                points: { show: false }
            },
            {
                label: "Paused",
                stroke: pausedColor,
                width: 2,
                paths: uPlot.paths.spline(),
                points: { show: false }
            },
            {
                label: "Max",
                stroke: thresholdColor,
                width: 1,
                dash: [6, 4],
                points: { show: false }
            },
            {
                label: "Min",
                stroke: thresholdColor,
                width: 1,
                dash: [6, 4],
                points: { show: false }
            }
        ]
    };

    chart = new uPlot(opts, chartData, container);
}

function loadChartHistory(data) {
    if (!chart) return;
    if (!data || data[0].length === 0) return;

    var timestamps = data[0];
    var percents = data[1];
    var maxes = data[2];
    var mins = data[3];
    var chargingFlags = data[4];

    var chargingSeries = [];
    var pausedSeries = [];

    for (var i = 0; i < timestamps.length; i++) {
        var isCharging = chargingFlags[i];
        var pct = percents[i];
        var prevCharging = i > 0 ? chargingFlags[i - 1] : isCharging;

        if (isCharging !== prevCharging && i > 0) {
            chargingSeries.push(pct);
            pausedSeries.push(pct);
        } else {
            chargingSeries.push(isCharging ? pct : null);
            pausedSeries.push(isCharging ? null : pct);
        }
    }

    chartData[0] = timestamps;
    chartData[1] = chargingSeries;
    chartData[2] = pausedSeries;
    chartData[3] = maxes;
    chartData[4] = mins;

    lastChartPercent = percents[percents.length - 1];
    lastChargingState = chargingFlags[chargingFlags.length - 1];

    chart.setData(chartData);
}

function pushChartPoint(s) {
    if (!chart) return;
    if (s.battery_percent === null || s.battery_percent === undefined) return;

    var stateChanged = s.is_charging !== lastChargingState;
    if (stateChanged) {
        lastChargingState = s.is_charging;
    }

    if (s.battery_percent === lastChartPercent && !stateChanged) return;
    lastChartPercent = s.battery_percent;

    var ts = Date.now() / 1000;
    var pct = s.battery_percent;

    if (stateChanged && chartData[0].length > 0) {
        chartData[1].push(pct);
        chartData[2].push(pct);
    } else {
        chartData[1].push(s.is_charging ? pct : null);
        chartData[2].push(s.is_charging ? null : pct);
    }

    chartData[0].push(ts);
    chartData[3].push(s.charge_max);
    chartData[4].push(s.charge_min);

    if (chartData[0].length > CHART_BUFFER_SIZE) {
        chartData[0].shift();
        chartData[1].shift();
        chartData[2].shift();
        chartData[3].shift();
        chartData[4].shift();
    }

    chart.setData(chartData);
}

function updateChartThresholds(max, min) {
    if (!chart || chartData[0].length === 0) return;
    for (var i = 0; i < chartData[3].length; i++) {
        chartData[3][i] = max;
        chartData[4][i] = min;
    }
    chart.setData(chartData);
}

function setupChartResize() {
    if (!chart) return;
    var container = document.getElementById("chart-container");
    var ro = new ResizeObserver(function() {
        chart.setSize({ width: container.clientWidth, height: 200 });
    });
    ro.observe(container);
}

// --- Init ---
connectWS();
initChart();
setupChartResize();
