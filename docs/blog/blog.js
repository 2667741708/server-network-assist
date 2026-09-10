const root = document.documentElement;
try {
  const saved = localStorage.getItem("network-notes-theme");
  if (saved) root.dataset.theme = saved;
} catch {}
document.querySelector("#theme").addEventListener("click", () => {
  root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  try {
    localStorage.setItem("network-notes-theme", root.dataset.theme);
  } catch {}
});
const updateRoute = () => {
  const shared = document.querySelector("#share-proxy").checked;
  document.querySelector("#route-client").textContent =
    document.querySelector("#client-os").value + " 客户端";
  document.querySelector("#route-gateway").textContent =
    document.querySelector("#gateway-os").value + " 出口";
  document.querySelector("#route-exit").textContent = shared
    ? "源 HTTP 代理 → 公网"
    : "公网";
  document.querySelector("#route-note").textContent = shared
    ? "共享代理：客户端通过隧道内 17897 端口访问源代理；支持系统代理的应用才使用这条路径。"
    : "仅共享网络：客户端原有代理设置保留，源机器的应用代理不会自动复制。";
};
document
  .querySelectorAll(".explorer select,.explorer input")
  .forEach((el) => el.addEventListener("change", updateRoute));
document.querySelectorAll(".copy").forEach((button) =>
  button.addEventListener("click", async () => {
    const box = button.closest(".codebox");
    const code = box.querySelector("code");
    try {
      await navigator.clipboard.writeText(code.textContent);
      box.querySelector(".copy-status").textContent = "命令已复制。";
    } catch {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(code);
      selection.removeAllRanges();
      selection.addRange(range);
      box.querySelector(".copy-status").textContent =
        "已选中命令，请按 Ctrl+C（macOS 使用 ⌘C）复制。";
    }
  }),
);
const viewer = document.querySelector("#image-viewer");
document.querySelectorAll(".zoom").forEach((link) =>
  link.addEventListener("click", (event) => {
    if (typeof viewer.showModal !== "function") return;
    event.preventDefault();
    const img = link.querySelector("img");
    const full = document.querySelector("#full-image");
    full.src = link.href;
    full.alt = img.alt;
    document.querySelector("#image-caption").textContent = img.alt;
    viewer.showModal();
  }),
);
document
  .querySelector("#close-image")
  .addEventListener("click", () => viewer.close());
const sections = [...document.querySelectorAll("main section[id]")];
const updateProgress = () => {
  const max = root.scrollHeight - window.innerHeight;
  document.querySelector("#progress").style.width =
    (max > 0 ? (window.scrollY / max) * 100 : 0) + "%";
  const current =
    sections
      .filter(
        (section) =>
          section.getBoundingClientRect().top < window.innerHeight * 0.4,
      )
      .at(-1) || sections[0];
  document.querySelectorAll(".rail nav a").forEach((link) => {
    const active = link.hash === "#" + current.id;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  });
};
window.addEventListener("scroll", updateProgress, { passive: true });
window.addEventListener("resize", updateProgress);
updateProgress();
