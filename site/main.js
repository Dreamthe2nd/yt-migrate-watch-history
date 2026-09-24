// Copy buttons for every code block
document.querySelectorAll(".copy-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const block = btn.closest(".codeblock");
    const pre = block && block.querySelector("pre");
    if (!pre) return;
    const done = (ok) => {
      btn.textContent = ok ? "Copied ✓" : "Copy failed";
      setTimeout(() => (btn.textContent = "Copy"), 1600);
    };
    try {
      await navigator.clipboard.writeText(pre.innerText);
      done(true);
    } catch {
      // fallback for non-secure contexts
      const range = document.createRange();
      range.selectNodeContents(pre);
      const sel = getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      done(document.execCommand("copy"));
      sel.removeAllRanges();
    }
  });
});

// Scrollspy: highlight the nav link for the section in view
const navLinks = Array.from(document.querySelectorAll(".nav-links a"));
const ids = navLinks.map((a) => a.getAttribute("href").slice(1));
const sections = ids.map((id) => document.getElementById(id)).filter(Boolean);
const setActive = (id) =>
  navLinks.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + id));

if ("IntersectionObserver" in window && sections.length) {
  const observer = new IntersectionObserver(
    (entries) => entries.forEach((e) => e.isIntersecting && setActive(e.target.id)),
    { rootMargin: "-35% 0px -60% 0px" }
  );
  sections.forEach((s) => observer.observe(s));
}
