/**
 * PocketLedger 记账页前端交互（陈常群模块）
 *
 * 职责：
 *   1. 收支类型联动——切换「收入 / 支出」时，类别下拉框换成对应类型的启用类别，
 *      当前已选类别若在新类型下不存在则清空，避免出现「支出选了工资」这类脏数据。
 *   2. 删除确认——拦截带 data-delete-entry 的删除表单，用原生 confirm 弹出
 *      「日期 / 类别 / 金额」，确认后才提交，防止误删。流水页（history）的删除按钮
 *      只要复用同样的 data 属性即可获得一致确认体验。
 *
 * 不依赖任何外部库，纯原生 JavaScript，符合现场断网演示要求。
 */
(() => {
  const form = document.getElementById("entry-form");
  const select = document.getElementById("category_id");
  const categoryData = document.getElementById("entry-categories");

  // ---------- 1. 收支类型联动 ----------
  function categoriesForType(type) {
    // 只保留该收支类型下仍启用的类别，保证新账可选集合干净。
    return categories.filter((cat) => cat.type === type && cat.active !== false);
  }

  function rebuildSelect(type) {
    if (!select) return;
    const previous = select.value;
    const options = categoriesForType(type);

    // 清空到只剩「请选择类别」占位项。
    select.length = 1;
    for (const cat of options) {
      const option = document.createElement("option");
      option.value = cat.id;
      option.textContent = cat.name;
      select.appendChild(option);
    }

    // 若之前的选择在新类型下仍存在则保留，否则清空。
    if (previous && options.some((cat) => cat.id === previous)) {
      select.value = previous;
    } else {
      select.value = "";
    }
  }

  // 优先从 JSON 数据节点解析；解析失败（极端情况）则从当前 <option> 兜底。
  let categories = [];
  if (categoryData && categoryData.textContent.trim()) {
    try {
      categories = JSON.parse(categoryData.textContent);
    } catch (err) {
      categories = [];
    }
  }
  if (!Array.isArray(categories) || categories.length === 0) {
    categories = Array.from(select ? select.options : []).map((option) => ({
      id: option.value,
      name: option.textContent,
      type: form ? form.elements.type.value : "expense",
      active: true,
    }));
  }

  if (form && select) {
    const radios = form.querySelectorAll('input[name="type"]');
    radios.forEach((radio) => {
      radio.addEventListener("change", () => rebuildSelect(radio.value));
    });

    // 同步选中态样式。
    const syncTypeStyle = () => {
      const checked = form.querySelector('input[name="type"]:checked');
      const value = checked ? checked.value : "expense";
      form.querySelectorAll(".pl-type-group label").forEach((label) => {
        label.classList.remove("selected-income", "selected-expense");
        if (value === "income") label.classList.add("selected-income");
        if (value === "expense") label.classList.add("selected-expense");
      });
    };
    form.querySelectorAll('input[name="type"]').forEach((radio) => {
      radio.addEventListener("change", syncTypeStyle);
    });
    syncTypeStyle();
  }

  // ---------- 2. 删除确认 ----------
  function confirmDelete(event) {
    const target = event.target.closest("form[data-delete-entry]");
    if (!target) return;
    const date = target.dataset.date || "";
    const category = target.dataset.category || "";
    const amount = target.dataset.amount || "";
    const message = "确定删除这笔账吗？\n" +
      "日期：" + date + "\n" +
      "类别：" + category + "\n" +
      "金额：¥" + amount;
    if (!window.confirm(message)) {
      event.preventDefault();
    }
  }

  // 用事件委托：既能覆盖记账页自带的删除按钮，也兼容流水页后续复用的按钮。
  document.addEventListener("submit", confirmDelete, true);
})();
