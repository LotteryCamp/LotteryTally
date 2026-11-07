(() => {
  const dataNode = document.getElementById("ledger-chart-data");
  const container = document.getElementById("expense-chart");
  if (!dataNode || !container) return;

  const currency = new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY"
  });

  function renderDailyChart(container, days) {
    if (!container) return;
    if (days.length === 0) {
      container.textContent = "当前筛选范围内暂无收支数据";
      return;
    }

    const left = 105;
    const right = 605;
    const top = 24;
    const bottom = 205;
    const maximum = days.reduce(
      (max, day) => Math.max(max, day.income_cents, day.expense_cents),
      0
    );
    const xAt = (index) => days.length === 1
      ? (left + right) / 2
      : left + index * (right - left) / (days.length - 1);
    const yAt = (amount) => bottom - amount * (bottom - top) / (maximum || 1);

    function svgElement(tag, attributes) {
      const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [name, value] of Object.entries(attributes)) {
        element.setAttribute(name, value);
      }
      return element;
    }

    const svg = svgElement("svg", {
      viewBox: "0 0 640 245",
      width: "100%",
      role: "img",
      "aria-label": "每日收入和支出趋势图"
    });

    function addLabel(text, x, y, anchor) {
      const label = svgElement("text", {
        x, y, "text-anchor": anchor, "font-size": 12, fill: "currentColor"
      });
      label.textContent = text;
      svg.append(label);
    }

    svg.append(svgElement("path", {
      d: "M " + left + " " + top + " V " + bottom + " H " + right,
      fill: "none",
      stroke: "currentColor"
    }));
    addLabel(currency.format(0), left - 8, bottom + 4, "end");
    if (maximum > 0) {
      addLabel(currency.format(maximum / 100), left - 8, top + 4, "end");
    }

    const labelCount = Math.min(days.length, 6);
    for (let i = 0; i < labelCount; i++) {
      const index = labelCount === 1
        ? 0
        : Math.round(i * (days.length - 1) / (labelCount - 1));
      addLabel(days[index].date, xAt(index), bottom + 23, "middle");
    }

    const series = [
      { key: "income_cents", name: "收入", color: "#047857" },
      { key: "expense_cents", name: "支出", color: "#b45309", dash: "6 4" }
    ];
    for (const { key, name, color, dash } of series) {
      const points = days.map((day, index) => xAt(index) + "," + yAt(day[key]));
      const line = svgElement("polyline", {
        points: points.join(" "),
        fill: "none",
        stroke: color,
        "stroke-width": 2
      });
      if (dash) line.setAttribute("stroke-dasharray", dash);
      svg.append(line);

      days.forEach((day, index) => {
        const dot = svgElement("circle", {
          cx: xAt(index), cy: yAt(day[key]), r: 3, fill: color
        });
        const title = svgElement("title", {});
        title.textContent = day.date + " " + name + " " + currency.format(day[key] / 100);
        dot.append(title);
        svg.append(dot);
      });
    }

    const legend = document.createElement("p");
    legend.textContent = "绿色实线表示收入，橙色虚线表示支出。";
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "查看每日收支明细";
    const table = document.createElement("table");
    const caption = table.createCaption();
    caption.textContent = "每日收入与支出（元）";
    const heading = table.createTHead().insertRow();
    for (const label of ["日期", "收入", "支出"]) {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = label;
      heading.append(cell);
    }

    const body = table.createTBody();
    for (const day of days) {
      const row = body.insertRow();
      row.insertCell().textContent = day.date;
      row.insertCell().textContent = currency.format(day.income_cents / 100);
      row.insertCell().textContent = currency.format(day.expense_cents / 100);
    }
    details.append(summary, table);
    container.append(legend, svg, details);
  }


  const data = JSON.parse(dataNode.textContent);
  renderDailyChart(document.getElementById("daily-chart"), data?.daily_totals ?? []);
  const expenses = (data?.category_expenses ?? [])
    .filter(({ amount_cents }) => amount_cents > 0)
    .sort((a, b) => b.amount_cents - a.amount_cents);

  if (expenses.length === 0) {
    container.textContent = "当前筛选范围内暂无支出";
    return;
  }

  const totalAmount = expenses.reduce((total, { amount_cents }) => total + amount_cents, 0);
  const percent = new Intl.NumberFormat("zh-CN", {
    style: "percent",
    minimumFractionDigits: 2
  });

  const colors = ["#2563eb", "#0f766e", "#b45309", "#7e22ce", "#be185d", "#475569", "#0369a1"];
  const bar = document.createElement("div");
  bar.style.display = "flex";
  bar.style.height = "1.25rem";
  bar.style.borderRadius = "0.375rem";
  bar.style.overflow = "hidden";
  bar.setAttribute("aria-hidden", "true");

  const legend = document.createElement("ul");
  legend.setAttribute("aria-label", "各类别支出金额与占比");
  legend.style.display = "flex";
  legend.style.flexWrap = "wrap";
  legend.style.gap = "0.5rem 1.25rem";
  legend.style.padding = "0";
  legend.style.listStyle = "none";

  expenses.forEach(({ category_name, label, amount_cents }, index) => {
    const share = amount_cents / totalAmount;
    const color = colors[index % colors.length];

    const segment = document.createElement("div");
    segment.style.flex = "0 0 " + share * 100 + "%";
    segment.style.backgroundColor = color;
    bar.append(segment);

    const item = document.createElement("li");
    const swatch = document.createElement("span");
    swatch.style.display = "inline-block";
    swatch.style.width = "0.75rem";
    swatch.style.height = "0.75rem";
    swatch.style.marginRight = "0.4rem";
    swatch.style.borderRadius = "0.125rem";
    swatch.style.backgroundColor = color;
    swatch.setAttribute("aria-hidden", "true");

    const caption = document.createElement("span");
    caption.textContent = (category_name ?? label ?? "未分类") + " " +
      currency.format(amount_cents / 100) + "（" + percent.format(share) + "）";
    item.append(swatch, caption);
    legend.append(item);
  });

  container.append(bar, legend);
})();
