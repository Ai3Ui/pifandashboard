(function () {
  "use strict";

  var TOKEN_KEY = "pifandashboard.session";
  var REFRESH_SECONDS = 10;
  var STATUS_TIMEOUT_MS = 7000;
  var MAX_HISTORY_POINTS = 60;
  var ALLOWED_GPIOS = [12, 13, 14, 18, 19];

  var pis = [];
  var authenticated = false;
  var authToken = "";
  var fanConfig = {
    gpio: 14,
    start_temp: 45,
    full_temp: 75,
    min_duty: 45,
    hysteresis: 2
  };
  var cards = new Map();
  var histories = new Map();
  var latestStatuses = new Map();
  var countdown = REFRESH_SECONDS;
  var clockTimer = null;
  var updateInFlight = false;
  var editingKey = "";
  var pendingDelete = null;
  var resizeTimer = null;

  function byId(id) {
    return document.getElementById(id);
  }

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function addText(parent, tag, className, text) {
    var node = element(tag, className, text);
    parent.appendChild(node);
    return node;
  }

  function setNotice(target, message, type) {
    var node = typeof target === "string" ? byId(target) : target;
    if (!node) {
      return;
    }
    node.textContent = message || "";
    node.className = "notice" + (type ? " " + type : "");
    node.hidden = !message;
  }

  function managerUrl(path) {
    return new URL(path, window.location.href).toString();
  }

  function getStoredToken() {
    try {
      return window.sessionStorage.getItem(TOKEN_KEY) || "";
    } catch (error) {
      return "";
    }
  }

  function storeToken(token) {
    try {
      window.sessionStorage.setItem(TOKEN_KEY, token);
    } catch (error) {
      throw new Error("This browser cannot store a secure session.");
    }
  }

  function clearToken() {
    authToken = "";
    try {
      window.sessionStorage.removeItem(TOKEN_KEY);
    } catch (error) {
      return;
    }
  }

  function openDialog(dialog) {
    if (dialog && !dialog.open) {
      dialog.showModal();
    }
  }

  function closeDialog(dialog) {
    if (dialog && dialog.open) {
      dialog.close();
    }
  }

  function stopPolling() {
    if (clockTimer !== null) {
      window.clearInterval(clockTimer);
      clockTimer = null;
    }
  }

  function requireLogin(message) {
    authenticated = false;
    clearToken();
    stopPolling();
    byId("appShell").hidden = true;
    setNotice("loginMessage", message || "", message ? "error" : "");
    byId("loginPassword").value = "";
    openDialog(byId("loginDialog"));
    window.setTimeout(function () {
      byId("loginPassword").focus();
    }, 0);
  }

  async function authFetch(path, options) {
    var requestOptions = options ? Object.assign({}, options) : {};
    var headers = new Headers(requestOptions.headers || {});
    if (authToken) {
      headers.set("Authorization", "Bearer " + authToken);
    }
    headers.set("Accept", "application/json");
    requestOptions.headers = headers;
    var response = await window.fetch(managerUrl(path), requestOptions);
    if (response.status === 401 && path !== "/auth/login") {
      requireLogin("Your session expired. Please sign in again.");
    }
    return response;
  }

  async function readResponseJson(response) {
    try {
      return await response.json();
    } catch (error) {
      return {};
    }
  }

  function errorFromResponse(response, data, fallback) {
    if (data && typeof data.error === "string" && data.error) {
      return data.error;
    }
    if (response.status === 429) {
      return "Too many attempts. Wait a moment and try again.";
    }
    return fallback + " (HTTP " + response.status + ")";
  }

  async function handleLogin(event) {
    event.preventDefault();
    var form = event.currentTarget;
    var submit = form.querySelector('button[type="submit"]');
    var password = byId("loginPassword").value;
    setNotice("loginMessage", "", "");
    submit.disabled = true;
    try {
      var response = await window.fetch(managerUrl("/auth/login"), {
        method: "POST",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ password: password })
      });
      var data = await readResponseJson(response);
      if (!response.ok) {
        throw new Error(errorFromResponse(response, data, "Sign-in failed"));
      }
      var token = data.token || data.access_token;
      if (typeof token !== "string" || token.length < 16) {
        throw new Error("The server did not return a valid session token.");
      }
      storeToken(token);
      authToken = token;
      authenticated = true;
      closeDialog(byId("loginDialog"));
      byId("appShell").hidden = false;
      byId("loginPassword").value = "";
      await startDashboard();
    } catch (error) {
      setNotice("loginMessage", error.message || "Could not sign in.", "error");
      byId("loginPassword").select();
    } finally {
      submit.disabled = false;
    }
  }

  async function verifySession() {
    authToken = getStoredToken();
    if (!authToken) {
      requireLogin("");
      return;
    }
    try {
      var response = await authFetch("/auth/check");
      if (!response.ok) {
        if (response.status !== 401) {
          requireLogin("The saved session could not be verified.");
        }
        return;
      }
      authenticated = true;
      closeDialog(byId("loginDialog"));
      byId("appShell").hidden = false;
      await startDashboard();
    } catch (error) {
      requireLogin("Could not reach the dashboard service.");
    }
  }

  async function logout() {
    var button = byId("logoutButton");
    button.disabled = true;
    try {
      await authFetch("/auth/logout", { method: "POST" });
    } catch (error) {
      return;
    } finally {
      button.disabled = false;
      requireLogin("Signed out.");
    }
  }

  function piKey(pi) {
    return String(pi.ip) + ":" + String(pi.port);
  }

  function finiteNumber(value, fallback) {
    var number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function bounded(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  function displayPercent(value) {
    return Number.isFinite(value) ? Math.round(value) + "%" : "—";
  }

  function displayTemperature(value) {
    return Number.isFinite(value) ? value.toFixed(1) + "°C" : "—";
  }

  function normalizeStatus(data) {
    var rawTemp = data.temperature !== undefined ? data.temperature : data.cpu_temp;
    var rawFan = data.speed !== undefined ? data.speed : (data.fan_duty !== undefined ? data.fan_duty : data.duty);
    var rawDisk = data.disk !== undefined ? data.disk : (data.disk_percent !== undefined ? data.disk_percent : data.storage);
    return {
      temperature: finiteNumber(rawTemp, NaN),
      fan: finiteNumber(rawFan, NaN),
      cpu: finiteNumber(data.cpu, NaN),
      memory: finiteNumber(data.memory, NaN),
      disk: finiteNumber(rawDisk, NaN),
      hostname: typeof data.hostname === "string" ? data.hostname : "",
      curve: data.curve && typeof data.curve === "object" ? data.curve : null,
      controllerError: typeof data.controller_error === "string" && data.controller_error ?
        data.controller_error : ""
    };
  }

  function curveForStatus(status) {
    var curve = status && status.curve ? status.curve : fanConfig;
    return {
      gpio: finiteNumber(curve.gpio, fanConfig.gpio),
      start_temp: finiteNumber(curve.start_temp, fanConfig.start_temp),
      full_temp: finiteNumber(curve.full_temp, fanConfig.full_temp),
      min_duty: finiteNumber(curve.min_duty, fanConfig.min_duty),
      hysteresis: finiteNumber(curve.hysteresis, fanConfig.hysteresis)
    };
  }

  async function fetchFleetStatus() {
    var controller = new AbortController();
    var timeout = window.setTimeout(function () {
      controller.abort();
    }, STATUS_TIMEOUT_MS);
    try {
      var response = await authFetch("/fleet-status", { signal: controller.signal });
      var data = await readResponseJson(response);
      if (!response.ok) {
        throw new Error(errorFromResponse(response, data, "Fleet status unavailable"));
      }
      if (!Array.isArray(data)) {
        throw new Error("The fleet status response was not valid.");
      }
      return data;
    } catch (error) {
      if (error && error.name === "AbortError") {
        throw new Error("The fleet status request timed out.");
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function currentServerPort() {
    if (window.location.port) {
      return Number(window.location.port);
    }
    return window.location.protocol === "https:" ? 443 : 80;
  }

  async function loadPiList() {
    var response = await authFetch("/get_pi_list");
    var data = await readResponseJson(response);
    if (!response.ok) {
      throw new Error(errorFromResponse(response, data, "Could not load the Pi list"));
    }
    if (!Array.isArray(data)) {
      throw new Error("The Pi list response was not valid.");
    }
    var normalized = data.filter(function (pi) {
      return pi && typeof pi.name === "string" && typeof pi.ip === "string" &&
        Number.isInteger(Number(pi.port));
    }).map(function (pi) {
      return { name: pi.name, ip: pi.ip, port: Number(pi.port) };
    });
    if (normalized.length !== data.length || normalized.some(function (pi) {
      return Boolean(validatePi(pi));
    })) {
      throw new Error("The Pi list contains invalid device details.");
    }
    pis = normalized;
    syncCards();
    renderPiManager();
  }

  function makeMetric(label, name) {
    var metric = element("div", "metric");
    addText(metric, "span", "metric-label", label);
    var value = addText(metric, "strong", "metric-value", "—");
    value.dataset.metric = name;
    return metric;
  }

  function makeLegend(items, className) {
    var legend = element("div", className);
    items.forEach(function (item) {
      var entry = element("span", "legend-item");
      var swatch = element("span", "legend-swatch series-" + item.series);
      swatch.setAttribute("aria-hidden", "true");
      entry.appendChild(swatch);
      addText(entry, "span", "", item.label);
      legend.appendChild(entry);
    });
    return legend;
  }

  function createVisual(title, canvasClass, accessibleLabel) {
    var figure = element("figure", "visual");
    addText(figure, "figcaption", "", title);
    var canvas = element("canvas", canvasClass);
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", accessibleLabel);
    figure.appendChild(canvas);
    return { figure: figure, canvas: canvas };
  }

  function createCard(pi) {
    var key = piKey(pi);
    var card = element("article", "pi-card");
    card.dataset.piKey = key;

    var header = element("header", "pi-card-header");
    var identity = element("div", "");
    var name = addText(identity, "h3", "", pi.name);
    var endpoint = addText(identity, "span", "endpoint", pi.ip + ":" + pi.port);
    var state = addText(header, "span", "connection-state", "Connecting");
    header.insertBefore(identity, state);
    card.appendChild(header);

    var body = element("div", "pi-card-body");
    var onlineContent = element("div", "online-content");
    var metrics = element("div", "metric-grid");
    metrics.appendChild(makeMetric("Temp", "temperature"));
    metrics.appendChild(makeMetric("Fan duty", "fan"));
    metrics.appendChild(makeMetric("CPU", "cpu"));
    metrics.appendChild(makeMetric("Memory", "memory"));
    metrics.appendChild(makeMetric("Disk", "disk"));
    onlineContent.appendChild(metrics);
    var controllerNotice = element("p", "controller-alert");
    controllerNotice.setAttribute("role", "alert");
    controllerNotice.hidden = true;
    onlineContent.appendChild(controllerNotice);

    var visualRow = element("div", "visual-row");
    var rings = createVisual("System resources", "viz-canvas resource-canvas", "CPU, memory and disk use");
    rings.figure.appendChild(makeLegend([
      { label: "CPU", series: "cpu" },
      { label: "Memory", series: "memory" },
      { label: "Disk", series: "disk" }
    ], "visual-legend"));
    var gauge = createVisual("Fan curve", "viz-canvas fan-canvas", "Fan curve and current temperature");
    visualRow.appendChild(rings.figure);
    visualRow.appendChild(gauge.figure);
    onlineContent.appendChild(visualRow);

    var historyDetails = element("details", "history-panel");
    addText(historyDetails, "summary", "", "Performance history");
    var historyCanvas = element("canvas", "history-canvas");
    historyCanvas.setAttribute("role", "img");
    historyCanvas.setAttribute("aria-label", "Recent temperature, fan, CPU and memory history");
    historyDetails.appendChild(historyCanvas);
    historyDetails.appendChild(makeLegend([
      { label: "Temp", series: "temperature" },
      { label: "Fan", series: "fan" },
      { label: "CPU", series: "cpu" },
      { label: "Memory", series: "memory" }
    ], "history-legend"));
    historyDetails.addEventListener("toggle", function () {
      if (historyDetails.open) {
        drawHistory(historyCanvas, histories.get(key));
      }
    });
    onlineContent.appendChild(historyDetails);

    var offlineContent = element("div", "offline-message");
    offlineContent.hidden = true;
    var offlineInner = element("div", "");
    var offlineTitle = addText(offlineInner, "strong", "", "Pi is offline");
    var offlineText = addText(offlineInner, "span", "", "Waiting for its status endpoint.");
    offlineContent.appendChild(offlineInner);

    body.appendChild(onlineContent);
    body.appendChild(offlineContent);
    card.appendChild(body);
    byId("fanStatus").appendChild(card);

    var refs = {
      card: card,
      name: name,
      endpoint: endpoint,
      state: state,
      online: onlineContent,
      offline: offlineContent,
      offlineTitle: offlineTitle,
      offlineText: offlineText,
      rings: rings.canvas,
      gauge: gauge.canvas,
      history: historyCanvas,
      historyDetails: historyDetails,
      controllerNotice: controllerNotice,
      metrics: {
        temperature: metrics.querySelector('[data-metric="temperature"]'),
        fan: metrics.querySelector('[data-metric="fan"]'),
        cpu: metrics.querySelector('[data-metric="cpu"]'),
        memory: metrics.querySelector('[data-metric="memory"]'),
        disk: metrics.querySelector('[data-metric="disk"]')
      }
    };
    cards.set(key, refs);
    return refs;
  }

  function syncCards() {
    var wanted = new Set(pis.map(piKey));
    cards.forEach(function (refs, key) {
      if (!wanted.has(key)) {
        refs.card.remove();
        cards.delete(key);
        histories.delete(key);
        latestStatuses.delete(key);
      }
    });
    pis.forEach(function (pi) {
      var key = piKey(pi);
      var refs = cards.get(key) || createCard(pi);
      refs.name.textContent = pi.name;
      refs.endpoint.textContent = pi.ip + ":" + pi.port;
    });
    byId("emptyState").hidden = pis.length !== 0;
    byId("fanStatus").hidden = pis.length === 0;
  }

  function updateCard(result) {
    var key = piKey(result.pi);
    var refs = cards.get(key) || createCard(result.pi);
    if (result.error || !result.status) {
      refs.card.classList.add("offline");
      refs.card.classList.remove("degraded");
      refs.state.textContent = "Offline";
      refs.online.hidden = true;
      refs.offline.hidden = false;
      refs.offlineTitle.textContent = "Pi is offline";
      refs.offlineText.textContent = result.error || "Status endpoint unavailable";
      return;
    }

    var status = result.status;
    latestStatuses.set(key, status);
    refs.card.classList.remove("offline");
    refs.card.classList.toggle("degraded", Boolean(status.controllerError));
    refs.state.textContent = status.controllerError ? "Fail-safe" : "Online";
    refs.online.hidden = false;
    refs.offline.hidden = true;
    refs.controllerNotice.hidden = !status.controllerError;
    refs.controllerNotice.textContent = status.controllerError ?
      "Cooling controller fail-safe: " + status.controllerError : "";
    refs.metrics.temperature.textContent = displayTemperature(status.temperature);
    refs.metrics.fan.textContent = displayPercent(status.fan);
    refs.metrics.cpu.textContent = displayPercent(status.cpu);
    refs.metrics.memory.textContent = displayPercent(status.memory);
    refs.metrics.disk.textContent = displayPercent(status.disk);
    refs.rings.setAttribute("aria-label",
      "CPU " + displayPercent(status.cpu) + ", memory " + displayPercent(status.memory) +
      ", disk " + displayPercent(status.disk));
    var curve = curveForStatus(status);
    refs.gauge.setAttribute("aria-label",
      "Temperature " + displayTemperature(status.temperature) + ", fan duty " + displayPercent(status.fan) +
      ", starts at " + curve.start_temp + " degrees and reaches full speed at " + curve.full_temp + " degrees");
    appendHistory(key, status);
    drawResourceRings(refs.rings, status);
    drawFanGauge(refs.gauge, status, curve);
    if (refs.historyDetails.open) {
      drawHistory(refs.history, histories.get(key));
    }
  }

  function appendHistory(key, status) {
    var history = histories.get(key);
    if (!history) {
      history = [];
      histories.set(key, history);
    }
    history.push({
      time: new Date(),
      temperature: status.temperature,
      fan: status.fan,
      cpu: status.cpu,
      memory: status.memory
    });
    if (history.length > MAX_HISTORY_POINTS) {
      history.splice(0, history.length - MAX_HISTORY_POINTS);
    }
  }

  async function pollStatuses() {
    if (!authenticated || updateInFlight || document.hidden) {
      return;
    }
    if (pis.length === 0) {
      byId("onlineCount").textContent = "0";
      byId("offlineCount").textContent = "0";
      return;
    }
    updateInFlight = true;
    try {
      var fleet = await fetchFleetStatus();
      if (!authenticated) {
        return;
      }
      var fleetByKey = new Map();
      fleet.forEach(function (item) {
        if (!item || typeof item.ip !== "string" || !Number.isInteger(Number(item.port))) {
          return;
        }
        var remotePi = {
          name: typeof item.name === "string" ? item.name : item.ip,
          ip: item.ip,
          port: Number(item.port)
        };
        var data = item.data && typeof item.data === "object" ? item.data : {};
        var remoteError = typeof item.error === "string" && item.error ? item.error :
          (typeof data.error === "string" && data.error ? data.error : "");
        fleetByKey.set(piKey(remotePi), {
          pi: remotePi,
          status: remoteError ? null : normalizeStatus(data),
          error: remoteError
        });
      });
      var results = pis.map(function (pi) {
        return fleetByKey.get(piKey(pi)) || {
          pi: pi,
          status: null,
          error: "No status was returned for this Pi"
        };
      });
      var online = 0;
      results.forEach(function (result) {
        updateCard(result);
        if (!result.error) {
          online += 1;
        }
      });
      byId("onlineCount").textContent = String(online);
      byId("offlineCount").textContent = String(results.length - online);
      byId("lastUpdate").textContent = "Updated " + new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
      });
      setNotice("dashboardMessage", "", "");
    } catch (error) {
      setNotice("dashboardMessage", "Status refresh failed: " + (error.message || "unknown error"), "error");
    } finally {
      updateInFlight = false;
      countdown = REFRESH_SECONDS;
      updateCountdown();
    }
  }

  function updateCountdown() {
    byId("countdown").textContent = document.hidden ? "Updates paused" : "Refresh in " + countdown + "s";
  }

  function startPolling() {
    stopPolling();
    countdown = REFRESH_SECONDS;
    updateCountdown();
    clockTimer = window.setInterval(function () {
      if (!authenticated || document.hidden) {
        updateCountdown();
        return;
      }
      countdown -= 1;
      if (countdown <= 0) {
        countdown = REFRESH_SECONDS;
        pollStatuses();
      }
      updateCountdown();
    }, 1000);
  }

  function cssColor(variable) {
    return window.getComputedStyle(document.documentElement).getPropertyValue(variable).trim();
  }

  function prepareCanvas(canvas) {
    if (!canvas || canvas.hidden || canvas.clientWidth < 2 || canvas.clientHeight < 2) {
      return null;
    }
    var width = Math.round(canvas.clientWidth);
    var height = Math.round(canvas.clientHeight);
    var ratio = Math.min(window.devicePixelRatio || 1, 2);
    var pixelWidth = Math.round(width * ratio);
    var pixelHeight = Math.round(height * ratio);
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }
    var context = canvas.getContext("2d");
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    context.lineCap = "round";
    context.lineJoin = "round";
    return { context: context, width: width, height: height };
  }

  function drawResourceRings(canvas, status) {
    var surface = prepareCanvas(canvas);
    if (!surface) {
      return;
    }
    var ctx = surface.context;
    var width = surface.width;
    var height = surface.height;
    var centerX = width / 2;
    var centerY = height / 2;
    var baseRadius = Math.max(28, Math.min(width, height) / 2 - 14);
    var ringGap = Math.max(11, Math.min(15, baseRadius / 4));
    var values = [
      { value: status.cpu, color: cssColor("--blue") },
      { value: status.memory, color: cssColor("--purple") },
      { value: status.disk, color: cssColor("--amber") }
    ];
    ctx.lineWidth = Math.max(6, Math.min(9, ringGap - 3));
    values.forEach(function (item, index) {
      var radius = baseRadius - index * ringGap;
      ctx.beginPath();
      ctx.strokeStyle = cssColor("--border");
      ctx.arc(centerX, centerY, radius, -Math.PI / 2, Math.PI * 1.5);
      ctx.stroke();
      if (Number.isFinite(item.value)) {
        ctx.beginPath();
        ctx.strokeStyle = item.color;
        ctx.arc(centerX, centerY, radius, -Math.PI / 2,
          -Math.PI / 2 + Math.PI * 2 * bounded(item.value, 0, 100) / 100);
        ctx.stroke();
      }
    });
    ctx.fillStyle = cssColor("--foreground");
    ctx.font = "700 17px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(displayPercent(status.cpu), centerX, centerY - 4);
    ctx.fillStyle = cssColor("--muted");
    ctx.font = "600 9px system-ui, sans-serif";
    ctx.fillText("CPU", centerX, centerY + 12);
  }

  function temperatureAngle(temperature) {
    var normalized = bounded((temperature - 20) / 70, 0, 1);
    return Math.PI + normalized * Math.PI;
  }

  function drawFanGauge(canvas, status, config) {
    var surface = prepareCanvas(canvas);
    if (!surface) {
      return;
    }
    var ctx = surface.context;
    var width = surface.width;
    var height = surface.height;
    var centerX = width / 2;
    var centerY = Math.min(height - 30, height * 0.72);
    var radius = Math.max(30, Math.min(width * 0.42, height * 0.55));
    var currentTemp = Number.isFinite(status.temperature) ? status.temperature : 20;
    var currentAngle = temperatureAngle(currentTemp);

    ctx.lineWidth = 11;
    ctx.beginPath();
    ctx.strokeStyle = cssColor("--border");
    ctx.arc(centerX, centerY, radius, Math.PI, Math.PI * 2);
    ctx.stroke();

    ctx.beginPath();
    ctx.strokeStyle = cssColor("--accent");
    ctx.arc(centerX, centerY, radius, Math.PI, currentAngle);
    ctx.stroke();

    [config.start_temp, config.full_temp].forEach(function (temperature, index) {
      var angle = temperatureAngle(Number(temperature));
      var x = centerX + Math.cos(angle) * radius;
      var y = centerY + Math.sin(angle) * radius;
      ctx.beginPath();
      ctx.fillStyle = index === 0 ? cssColor("--amber") : cssColor("--red");
      ctx.arc(x, y, 4.5, 0, Math.PI * 2);
      ctx.fill();
    });

    var needleLength = radius - 16;
    ctx.beginPath();
    ctx.lineWidth = 2;
    ctx.strokeStyle = cssColor("--foreground");
    ctx.moveTo(centerX, centerY);
    ctx.lineTo(centerX + Math.cos(currentAngle) * needleLength,
      centerY + Math.sin(currentAngle) * needleLength);
    ctx.stroke();
    ctx.beginPath();
    ctx.fillStyle = cssColor("--foreground");
    ctx.arc(centerX, centerY, 4, 0, Math.PI * 2);
    ctx.fill();

    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = cssColor("--foreground");
    ctx.font = "700 17px system-ui, sans-serif";
    ctx.fillText(displayPercent(status.fan), centerX, centerY + 20);
    ctx.fillStyle = cssColor("--muted");
    ctx.font = "600 9px system-ui, sans-serif";
    ctx.fillText("20°", centerX - radius, centerY + 14);
    ctx.fillText("90°", centerX + radius, centerY + 14);
    ctx.fillText(config.start_temp + "° start", centerX, Math.max(10, centerY - radius - 8));
  }

  function drawHistory(canvas, history) {
    var surface = prepareCanvas(canvas);
    if (!surface) {
      return;
    }
    var ctx = surface.context;
    var width = surface.width;
    var height = surface.height;
    var padding = { left: 34, right: 8, top: 10, bottom: 24 };
    var plotWidth = width - padding.left - padding.right;
    var plotHeight = height - padding.top - padding.bottom;
    var series = [
      { key: "temperature", color: cssColor("--accent") },
      { key: "fan", color: cssColor("--green") },
      { key: "cpu", color: cssColor("--blue") },
      { key: "memory", color: cssColor("--purple") }
    ];

    ctx.font = "9px system-ui, sans-serif";
    ctx.fillStyle = cssColor("--muted");
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    [0, 25, 50, 75, 100].forEach(function (tick) {
      var y = padding.top + plotHeight * (1 - tick / 100);
      ctx.beginPath();
      ctx.lineWidth = 1;
      ctx.strokeStyle = cssColor("--border");
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
      ctx.fillText(String(tick), padding.left - 6, y);
    });

    if (!history || history.length === 0) {
      ctx.textAlign = "center";
      ctx.fillText("History appears after the next update", padding.left + plotWidth / 2,
        padding.top + plotHeight / 2);
      return;
    }

    series.forEach(function (item) {
      ctx.beginPath();
      ctx.lineWidth = 2;
      ctx.strokeStyle = item.color;
      var points = 0;
      history.forEach(function (sample, index) {
        var value = sample[item.key];
        if (!Number.isFinite(value)) {
          return;
        }
        var x = padding.left + (history.length === 1 ? plotWidth / 2 : plotWidth * index / (history.length - 1));
        var y = padding.top + plotHeight * (1 - bounded(value, 0, 100) / 100);
        if (points === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
        points += 1;
      });
      if (points > 1) {
        ctx.stroke();
      } else if (points === 1) {
        ctx.stroke();
      }
    });

    ctx.fillStyle = cssColor("--muted");
    ctx.font = "9px system-ui, sans-serif";
    ctx.textBaseline = "bottom";
    ctx.textAlign = "left";
    ctx.fillText(history[0].time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      padding.left, height - 2);
    ctx.textAlign = "right";
    ctx.fillText(history[history.length - 1].time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      width - padding.right, height - 2);
  }

  function redrawVisibleCanvases() {
    cards.forEach(function (refs, key) {
      var status = latestStatuses.get(key);
      if (!status || refs.online.hidden) {
        return;
      }
      drawResourceRings(refs.rings, status);
      drawFanGauge(refs.gauge, status, curveForStatus(status));
      if (refs.historyDetails.open) {
        drawHistory(refs.history, histories.get(key));
      }
    });
  }

  async function loadFanConfig() {
    setNotice("fanConfigMessage", "", "");
    try {
      var response = await authFetch("/fan-config");
      var data = await readResponseJson(response);
      if (!response.ok) {
        throw new Error(errorFromResponse(response, data, "Could not load fan settings"));
      }
      var config = data.config && typeof data.config === "object" ? data.config : data;
      fanConfig = {
        gpio: finiteNumber(config.gpio, 14),
        start_temp: finiteNumber(config.start_temp, 45),
        full_temp: finiteNumber(config.full_temp, 75),
        min_duty: finiteNumber(config.min_duty, 45),
        hysteresis: finiteNumber(config.hysteresis, 2)
      };
      byId("fanGpio").value = String(fanConfig.gpio);
      byId("fanStartTemp").value = String(fanConfig.start_temp);
      byId("fanFullTemp").value = String(fanConfig.full_temp);
      byId("fanMinDuty").value = String(fanConfig.min_duty);
      byId("fanHysteresis").value = String(fanConfig.hysteresis);
      redrawVisibleCanvases();
    } catch (error) {
      setNotice("fanConfigMessage", error.message || "Could not load fan settings.", "error");
    }
  }

  function readFanForm() {
    return {
      gpio: Number(byId("fanGpio").value),
      start_temp: Number(byId("fanStartTemp").value),
      full_temp: Number(byId("fanFullTemp").value),
      min_duty: Number(byId("fanMinDuty").value),
      hysteresis: Number(byId("fanHysteresis").value)
    };
  }

  function validateFanConfig(config) {
    if (ALLOWED_GPIOS.indexOf(config.gpio) === -1) {
      return "Choose one of the supported GPIO control outputs.";
    }
    if (config.start_temp < 20 || config.start_temp > 75) {
      return "The start temperature must be between 20°C and 75°C.";
    }
    if (config.full_temp < config.start_temp + 5 || config.full_temp > 90) {
      return "Full speed must be at least 5°C above the start temperature and no higher than 90°C.";
    }
    if (config.min_duty < 35 || config.min_duty > 100) {
      return "Starting speed must be between 35% and 100%.";
    }
    if (config.hysteresis < 0 || config.hysteresis > 10) {
      return "Hysteresis must be between 0°C and 10°C.";
    }
    return "";
  }

  async function saveFanConfig(event) {
    event.preventDefault();
    var form = event.currentTarget;
    var submit = form.querySelector('button[type="submit"]');
    var config = readFanForm();
    var validation = validateFanConfig(config);
    if (validation) {
      setNotice("fanConfigMessage", validation, "error");
      return;
    }
    submit.disabled = true;
    setNotice("fanConfigMessage", "", "");
    try {
      var response = await authFetch("/fan-config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config)
      });
      var data = await readResponseJson(response);
      if (!response.ok) {
        throw new Error(errorFromResponse(response, data, "Could not save fan settings"));
      }
      fanConfig = Object.assign({}, config, data.config || data);
      setNotice("fanConfigMessage", "Fan curve saved. The controller will apply it within five seconds.", "success");
      redrawVisibleCanvases();
    } catch (error) {
      setNotice("fanConfigMessage", error.message || "Could not save fan settings.", "error");
    } finally {
      submit.disabled = false;
    }
  }

  function validIpv4(address) {
    var parts = String(address).split(".");
    return parts.length === 4 && parts.every(function (part) {
      return /^\d{1,3}$/.test(part) && Number(part) >= 0 && Number(part) <= 255 &&
        String(Number(part)) === part;
    });
  }

  function validPiName(name) {
    return /^[A-Za-z0-9 ._-]{1,40}$/.test(name);
  }

  function validatePi(pi) {
    if (!validPiName(pi.name)) {
      return "Names may contain letters, numbers, spaces, dots, underscores and hyphens.";
    }
    if (!validIpv4(pi.ip)) {
      return "Enter a valid IPv4 address, for example 192.168.1.10.";
    }
    if (!Number.isInteger(pi.port) || pi.port < 1024 || pi.port > 65535) {
      return "The port must be between 1024 and 65535.";
    }
    return "";
  }

  function readPiForm(form) {
    return {
      name: form.elements.name.value.trim(),
      ip: form.elements.ip.value.trim(),
      port: Number(form.elements.port.value)
    };
  }

  async function addPi(event) {
    event.preventDefault();
    var form = event.currentTarget;
    var pi = readPiForm(form);
    var validation = validatePi(pi);
    if (validation) {
      setNotice("piManagerMessage", validation, "error");
      return;
    }
    var submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      var response = await authFetch("/add_pi", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pi)
      });
      var data = await readResponseJson(response);
      if (!response.ok) {
        throw new Error(errorFromResponse(response, data, "Could not add the Pi"));
      }
      form.reset();
      byId("piPortInput").value = String(currentServerPort() >= 1024 ? currentServerPort() : 8088);
      setNotice("piManagerMessage", pi.name + " was added.", "success");
      await loadPiList();
      await pollStatuses();
    } catch (error) {
      setNotice("piManagerMessage", error.message || "Could not add the Pi.", "error");
    } finally {
      submit.disabled = false;
    }
  }

  function makePiField(labelText, name, type, value) {
    var field = element("div", "field");
    var inputId = "edit-" + name + "-" + Math.random().toString(36).slice(2);
    var label = addText(field, "label", "", labelText);
    label.htmlFor = inputId;
    var input = element("input", "");
    input.id = inputId;
    input.name = name;
    input.type = type;
    input.value = String(value);
    input.required = true;
    if (name === "name") {
      input.maxLength = 40;
    }
    if (name === "port") {
      input.min = "1024";
      input.max = "65535";
    }
    field.appendChild(input);
    return field;
  }

  function renderPiManager() {
    var list = byId("piListDisplay");
    list.replaceChildren();
    if (pis.length === 0) {
      list.appendChild(element("p", "pi-list-empty", "No Pis are configured yet."));
      return;
    }

    pis.forEach(function (pi) {
      var key = piKey(pi);
      var row = element("div", "pi-list-item");
      if (editingKey === key) {
        var form = element("form", "pi-edit-form");
        form.method = "dialog";
        form.appendChild(makePiField("Name", "name", "text", pi.name));
        form.appendChild(makePiField("IPv4 address", "ip", "text", pi.ip));
        form.appendChild(makePiField("Port", "port", "number", pi.port));
        var actions = element("div", "pi-list-actions");
        var cancel = element("button", "button ghost", "Cancel");
        cancel.type = "button";
        cancel.addEventListener("click", function () {
          editingKey = "";
          renderPiManager();
        });
        var save = element("button", "button primary", "Save");
        save.type = "submit";
        actions.appendChild(cancel);
        actions.appendChild(save);
        form.appendChild(actions);
        form.addEventListener("submit", function (event) {
          editPi(event, pi);
        });
        row.appendChild(form);
      } else {
        var identity = element("div", "");
        addText(identity, "strong", "", pi.name);
        addText(identity, "span", "", pi.ip + ":" + pi.port);
        var buttons = element("div", "pi-list-actions");
        var edit = element("button", "button", "Edit");
        edit.type = "button";
        edit.addEventListener("click", function () {
          editingKey = key;
          renderPiManager();
        });
        var remove = element("button", "button ghost", "Remove");
        remove.type = "button";
        remove.addEventListener("click", function () {
          pendingDelete = pi;
          byId("deleteMessage").textContent = "This removes " + pi.name + " (" + pi.ip + ") from the dashboard. It does not uninstall anything from that Pi.";
          openDialog(byId("deleteDialog"));
        });
        buttons.appendChild(edit);
        buttons.appendChild(remove);
        row.appendChild(identity);
        row.appendChild(buttons);
      }
      list.appendChild(row);
    });
  }

  async function editPi(event, originalPi) {
    event.preventDefault();
    var form = event.currentTarget;
    var updated = readPiForm(form);
    var validation = validatePi(updated);
    if (validation) {
      setNotice("piManagerMessage", validation, "error");
      return;
    }
    var submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      var payload = Object.assign({ originalIp: originalPi.ip }, updated);
      var response = await authFetch("/edit_pi", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      var data = await readResponseJson(response);
      if (!response.ok) {
        throw new Error(errorFromResponse(response, data, "Could not update the Pi"));
      }
      editingKey = "";
      setNotice("piManagerMessage", updated.name + " was updated.", "success");
      await loadPiList();
      await pollStatuses();
    } catch (error) {
      setNotice("piManagerMessage", error.message || "Could not update the Pi.", "error");
      submit.disabled = false;
    }
  }

  async function deletePi(event) {
    event.preventDefault();
    if (!pendingDelete) {
      closeDialog(byId("deleteDialog"));
      return;
    }
    var pi = pendingDelete;
    var submit = event.currentTarget.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      var response = await authFetch("/delete_pi", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip: pi.ip })
      });
      var data = await readResponseJson(response);
      if (!response.ok) {
        throw new Error(errorFromResponse(response, data, "Could not remove the Pi"));
      }
      closeDialog(byId("deleteDialog"));
      pendingDelete = null;
      setNotice("piManagerMessage", pi.name + " was removed.", "success");
      await loadPiList();
      await pollStatuses();
    } catch (error) {
      closeDialog(byId("deleteDialog"));
      setNotice("piManagerMessage", error.message || "Could not remove the Pi.", "error");
    } finally {
      submit.disabled = false;
    }
  }

  function applyLayout() {
    var useThree = false;
    try {
      useThree = window.localStorage.getItem("pifandashboard.layout") === "three";
    } catch (error) {
      useThree = false;
    }
    var grid = byId("fanStatus");
    grid.classList.toggle("layout-three", useThree);
    grid.classList.toggle("layout-two", !useThree);
    var button = byId("layoutToggleButton");
    button.setAttribute("aria-pressed", String(useThree));
    button.textContent = useThree ? "2 columns" : "3 columns";
  }

  function toggleLayout() {
    var currentlyThree = byId("fanStatus").classList.contains("layout-three");
    try {
      window.localStorage.setItem("pifandashboard.layout", currentlyThree ? "two" : "three");
    } catch (error) {
      return;
    } finally {
      applyLayout();
      window.setTimeout(redrawVisibleCanvases, 50);
    }
  }

  async function openFanSettings() {
    openDialog(byId("fanSettingsDialog"));
    await loadFanConfig();
  }

  async function openPiManager() {
    setNotice("piManagerMessage", "", "");
    openDialog(byId("piListDialog"));
    try {
      await loadPiList();
    } catch (error) {
      setNotice("piManagerMessage", error.message || "Could not load the Pi list.", "error");
    }
  }

  async function startDashboard() {
    applyLayout();
    setNotice("dashboardMessage", "", "");
    try {
      await Promise.all([loadPiList(), loadFanConfig()]);
      await pollStatuses();
      startPolling();
    } catch (error) {
      if (authenticated) {
        setNotice("dashboardMessage", error.message || "Could not initialise the dashboard.", "error");
        startPolling();
      }
    }
  }

  function bindEvents() {
    byId("loginForm").addEventListener("submit", handleLogin);
    byId("loginDialog").addEventListener("cancel", function (event) {
      event.preventDefault();
    });
    byId("logoutButton").addEventListener("click", logout);
    byId("fanSettingsButton").addEventListener("click", openFanSettings);
    byId("managePisButton").addEventListener("click", openPiManager);
    byId("emptyAddButton").addEventListener("click", openPiManager);
    byId("layoutToggleButton").addEventListener("click", toggleLayout);
    byId("fanSettingsForm").addEventListener("submit", saveFanConfig);
    byId("addPiForm").addEventListener("submit", addPi);
    byId("deleteForm").addEventListener("submit", deletePi);

    document.querySelectorAll("[data-close-dialog]").forEach(function (button) {
      button.addEventListener("click", function () {
        closeDialog(byId(button.dataset.closeDialog));
      });
    });

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && authenticated) {
        countdown = REFRESH_SECONDS;
        pollStatuses();
        window.setTimeout(redrawVisibleCanvases, 0);
      }
      updateCountdown();
    });

    window.addEventListener("resize", function () {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(redrawVisibleCanvases, 120);
    }, { passive: true });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindEvents();
    verifySession();
  });
}());
