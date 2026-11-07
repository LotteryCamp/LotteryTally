(() => {
  const dataNode = document.getElementById("ledger-chart-data");
  const container = document.getElementById("expense-chart");
  if (!dataNode || !container) return;

  const data = JSON.parse(dataNode.textContent);
  const expenses = (data?.category_expenses ?? [])
    .filter(({ amount_cents }) => amount_cents > 0)
    .sort((a, b) => b.amount_cents - a.amount_cents);

  if (expenses.length === 0) {
    container.textContent = "当前筛选范围内暂无支出";
    return;
  }

  const currency = new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY"
  });
  const maxAmount = expenses[0].amount_cents;

  for (const { label, amount_cents } of expenses) {
    const row = document.createElement("div");
    row.style.marginBottom = "0.75rem";

    const caption = document.createElement("div");
    caption.textContent = label + " " + currency.format(amount_cents / 100);

    const bar = document.createElement("div");
    bar.style.width = (amount_cents / maxAmount) * 100 + "%";
    bar.style.height = "0.75rem";
    bar.style.backgroundColor = "#2563eb";
    bar.style.borderRadius = "0.25rem";
    bar.setAttribute("aria-hidden", "true");

    row.append(caption, bar);
    container.append(row);
  }
})();
