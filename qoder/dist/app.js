const screens = {
  overview: {
    image: "/assets/overview.png",
    alt: "知溯今日概览真实运行截图",
    title: "一个页面，看清学习进度",
    caption: "知识条目、索引片段、待复习和掌握度来自 seekdb 中的真实记录。",
  },
  search: {
    image: "/assets/search.png",
    alt: "知溯混合检索真实运行截图",
    title: "每个答案，都能回到原文",
    caption: "实际查询“怎样对抗遗忘”，右侧展示 HNSW、ngram、RRF、命中 ID 和审计事件。",
  },
  review: {
    image: "/assets/review.png",
    alt: "知溯复习空间真实运行截图",
    title: "先回忆，再核对",
    caption: "复习反馈会真实更新 seekdb 中的掌握度、复习间隔和到期时间。",
  },
};

const demoImage = document.querySelector("#demo-image");
const demoCaption = document.querySelector("#demo-caption");
document.querySelectorAll("[data-screen]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-screen]").forEach((item) => item.classList.toggle("active", item === button));
    const screen = screens[button.dataset.screen];
    demoImage.src = screen.image;
    demoImage.alt = screen.alt;
    demoCaption.innerHTML = `<strong>${screen.title}</strong><span>${screen.caption}</span>`;
  });
});

document.querySelector("#copy-command").addEventListener("click", async () => {
  await navigator.clipboard.writeText(document.querySelector("#command-text").textContent);
  const toast = document.querySelector("#toast");
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 1800);
});
