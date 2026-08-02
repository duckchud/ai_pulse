(function () {
  "use strict";

  var EMPTY_MESSAGE = "해당 기준에서 관측된 결과 없음";
  var ERROR_MESSAGE = "차트를 표시할 수 없습니다. 아래 표를 확인하세요.";
  var PLOTLY_CONFIG = {
    responsive: true,
    displaylogo: false,
    modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d", "toggleSpikelines"],
  };
  var COLORS = ["#2563eb", "#059669", "#dc2626", "#d97706", "#7c3aed", "#0891b2"];

  function validRows(rows) {
    return Array.isArray(rows) ? rows.filter(function (row) {
      return row && typeof row === "object" && !Array.isArray(row);
    }) : [];
  }

  function number(value) {
    if (
      value === null ||
      value === undefined ||
      typeof value === "boolean" ||
      (typeof value === "string" && value.trim() === "")
    ) {
      return null;
    }
    var parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function text(value, fallback) {
    if (value === null || value === undefined || value === "") {
      return fallback || "-";
    }
    return String(value);
  }

  function bucketLabel(value) {
    var timestamp = number(value);
    if (timestamp === null || timestamp < 1000000000) {
      return text(value);
    }
    var date = new Date(timestamp * 1000);
    return Number.isNaN(date.getTime()) ? text(value) : date.toISOString().slice(0, 10);
  }

  function modelLabel(row, suffix) {
    var fields = suffix ? ["vendor_" + suffix, "family_" + suffix, "version_" + suffix] : ["vendor", "family", "version"];
    var parts = fields.map(function (field) { return row[field]; }).filter(function (value) {
      return value !== null && value !== undefined && value !== "";
    });
    return parts.length ? parts.map(String).join("/") : text(row.group_label);
  }

  function renderEmptyState(element) {
    if (!element) {
      return;
    }
    element.replaceChildren();
    var message = document.createElement("p");
    message.className = "chart-empty";
    message.textContent = EMPTY_MESSAGE;
    element.appendChild(message);
  }

  function renderErrorState(element) {
    if (!element) {
      return;
    }
    element.replaceChildren();
    var message = document.createElement("p");
    message.className = "chart-empty";
    message.textContent = ERROR_MESSAGE;
    element.appendChild(message);
  }

  function baseLayout(options) {
    options = options || {};
    return {
      height: options.height || 340,
      margin: { l: options.leftMargin || 80, r: 24, t: 24, b: 52 },
      paper_bgcolor: "#ffffff",
      plot_bgcolor: "#ffffff",
      font: { color: "#202124", family: "Malgun Gothic, Noto Sans KR, sans-serif" },
      hoverlabel: { align: "left" },
      showlegend: options.showlegend !== false,
      legend: { orientation: "h", y: -0.22 },
      xaxis: { automargin: true, gridcolor: "#e5e7eb", zerolinecolor: "#9ca3af" },
      yaxis: { automargin: true, gridcolor: "#e5e7eb", zerolinecolor: "#9ca3af" },
      barmode: options.barmode,
    };
  }

  function plot(element, traces, options) {
    if (!element || !window.Plotly || typeof window.Plotly.newPlot !== "function") {
      renderErrorState(element);
      return;
    }
    try {
      Promise.resolve(
        window.Plotly.newPlot(element, traces, baseLayout(options), PLOTLY_CONFIG)
      ).catch(function () {
        renderErrorState(element);
      });
    } catch (error) {
      renderErrorState(element);
    }
  }

  function renderTimeseries(rows, element, options) {
    var grouped = {};
    var buckets = {};
    validRows(rows).forEach(function (row) {
      var bucket = row.bucket_start;
      var count = number(row.story_count);
      var bucketText = bucketLabel(bucket);
      var label = text(row.group_label || row.family);
      if (bucket === null || bucket === undefined || bucket === "" || count === null) {
        return;
      }
      grouped[label] = grouped[label] || {};
      grouped[label][bucketText] = count;
      buckets[bucketText] = true;
    });
    var labels = Object.keys(grouped).sort();
    var bucketLabels = Object.keys(buckets).sort();
    if (!labels.length) {
      renderEmptyState(element);
      return;
    }
    var traces = labels.map(function (label, index) {
      return {
        type: "scatter",
        mode: "lines+markers",
        name: label,
        x: bucketLabels,
        y: bucketLabels.map(function (bucket) {
          return Object.prototype.hasOwnProperty.call(grouped[label], bucket) ? grouped[label][bucket] : 0;
        }),
        line: { color: COLORS[index % COLORS.length] },
        hovertemplate: "%{x}<br>고유 story: %{y}<extra>" + label + "</extra>",
      };
    });
    plot(element, traces, Object.assign({ showlegend: true }, options));
  }

  function renderEmerging(rows, element, options) {
    var values = validRows(rows).map(function (row) {
      return { label: text(row.group_label), value: number(row.mention_delta), recent: number(row.recent_story_count) };
    }).filter(function (row) { return row.value !== null; }).sort(function (left, right) {
      return right.value - left.value || left.label.localeCompare(right.label);
    });
    if (!values.length) {
      renderEmptyState(element);
      return;
    }
    plot(element, [{
      type: "bar",
      orientation: "h",
      y: values.map(function (row) { return row.label; }).reverse(),
      x: values.map(function (row) { return row.value; }).reverse(),
      marker: { color: "#059669" },
      customdata: values.map(function (row) { return row.recent; }).reverse(),
      hovertemplate: "%{y}<br>언급 증감: %{x}<br>최근 story: %{customdata}<extra></extra>",
    }], Object.assign({ showlegend: false, leftMargin: 150 }, options));
  }

  function renderLineup(rows, element, options) {
    var values = validRows(rows).map(function (row) {
      return { label: modelLabel(row), value: number(row.weighted_count), stories: number(row.story_count) };
    }).filter(function (row) { return row.value !== null; }).sort(function (left, right) {
      return right.value - left.value || left.label.localeCompare(right.label);
    });
    if (!values.length) {
      renderEmptyState(element);
      return;
    }
    plot(element, [{
      type: "bar",
      orientation: "h",
      y: values.map(function (row) { return row.label; }).reverse(),
      x: values.map(function (row) { return row.value; }).reverse(),
      marker: { color: "#2563eb" },
      customdata: values.map(function (row) { return row.stories; }).reverse(),
      hovertemplate: "%{y}<br>가중 story: %{x:.2f}<br>누적 story: %{customdata}<extra></extra>",
    }], Object.assign({ showlegend: false, leftMargin: 180 }, options));
  }

  function renderCooccurrence(rows, element, options) {
    var pairs = validRows(rows).map(function (row) {
      var count = number(row.story_count);
      var left = modelLabel(row, "a");
      var right = modelLabel(row, "b");
      return count === null || left === "-" || right === "-" ? null : { left: left, right: right, count: count };
    }).filter(Boolean).sort(function (left, right) {
      return right.count - left.count || (left.left + left.right).localeCompare(right.left + right.right);
    });
    if (!pairs.length) {
      renderEmptyState(element);
      return;
    }
    var labels = Array.from(new Set(pairs.flatMap(function (pair) { return [pair.left, pair.right]; }))).sort();
    if (labels.length >= 3 && pairs.length >= 2) {
      var matrix = labels.map(function () { return labels.map(function () { return 0; }); });
      pairs.forEach(function (pair) {
        var leftIndex = labels.indexOf(pair.left);
        var rightIndex = labels.indexOf(pair.right);
        matrix[leftIndex][rightIndex] = pair.count;
        matrix[rightIndex][leftIndex] = pair.count;
      });
      plot(element, [{
        type: "heatmap",
        x: labels,
        y: labels,
        z: matrix,
        colorscale: [[0, "#eff6ff"], [1, "#1d4ed8"]],
        hovertemplate: "%{y} + %{x}<br>공유 story: %{z}<extra></extra>",
      }], Object.assign({ showlegend: false, leftMargin: 130 }, options));
      return;
    }
    plot(element, [{
      type: "bar",
      orientation: "h",
      y: pairs.map(function (pair) { return pair.left + " + " + pair.right; }).reverse(),
      x: pairs.map(function (pair) { return pair.count; }).reverse(),
      marker: { color: "#7c3aed" },
      hovertemplate: "%{y}<br>공유 story: %{x}<extra></extra>",
    }], Object.assign({ showlegend: false, leftMargin: 220 }, options));
  }

  function renderFraming(rows, element, options) {
    var cells = {};
    var labels = [];
    var stanceLabels = [];
    validRows(rows).forEach(function (row) {
      if (!cells) {
        return;
      }
      var label = text(row.group_label);
      var stance = text(row.stance);
      var count = number(row.story_count);
      if (label === "-" || stance === "-" || count === null) {
        return;
      }
      var key = label + "\u0000" + stance;
      if (Object.prototype.hasOwnProperty.call(cells, key)) {
        renderErrorState(element);
        cells = null;
        return;
      }
      cells[key] = count;
      if (labels.indexOf(label) === -1) {
        labels.push(label);
      }
      if (stanceLabels.indexOf(stance) === -1) {
        stanceLabels.push(stance);
      }
    });
    if (!cells) {
      return;
    }
    if (!labels.length || !stanceLabels.length) {
      renderEmptyState(element);
      return;
    }
    var traces = stanceLabels.map(function (stance, index) {
      return {
        type: "bar",
        orientation: "h",
        name: stance,
        y: labels.slice().reverse(),
        x: labels.map(function (label) { return cells[label + "\u0000" + stance] || 0; }).reverse(),
        marker: { color: COLORS[index % COLORS.length] },
        hovertemplate: "%{y}<br>" + stance + ": %{x}<extra></extra>",
      };
    });
    plot(element, traces, Object.assign({ barmode: "stack", leftMargin: 150 }, options));
  }

  function readReportData() {
    var source = document.getElementById("report-data");
    if (!source) {
      return null;
    }
    try {
      var reportData = JSON.parse(source.textContent || "{}");
      return reportData && typeof reportData === "object" ? reportData : null;
    } catch (error) {
      return null;
    }
  }

  window.renderAiPulseDashboard = function () {
    var reportData = readReportData();
    if (!reportData) {
      return;
    }
    renderTimeseries(reportData.timeseries, document.getElementById("chart-timeseries"));
    renderEmerging(reportData.emerging, document.getElementById("chart-emerging"));
    renderLineup(reportData.lineup, document.getElementById("chart-lineup"));
    renderCooccurrence(reportData.cooccurrence, document.getElementById("chart-cooccurrence"));
    renderFraming(reportData.framing, document.getElementById("chart-framing"));
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", window.renderAiPulseDashboard, { once: true });
  } else {
    window.renderAiPulseDashboard();
  }
}());
