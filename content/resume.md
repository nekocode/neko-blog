---
title: 简历
---

<iframe id="resume-frame" src="/_resume/" title="简历" scrolling="no"
  style="width:100%;border:1px solid #ddd;border-radius:4px;display:block;min-height:100vh"></iframe>

<script>
// 同源 iframe：用 ResizeObserver 观测源页 body，任何回流（窗口缩放、断点切换、
// 字体/图片异步加载、动态内容）都自动回填高度。body 自然塌缩，无需归零 hack。
(function () {
  var frame = document.getElementById("resume-frame");
  var observer = null;
  function bind() {
    try {
      var doc = frame.contentDocument || frame.contentWindow.document;
      var body = doc.body;
      if (observer) observer.disconnect();
      observer = new ResizeObserver(function () {
        frame.style.height = body.scrollHeight + "px";
      });
      observer.observe(body);
    } catch (error) {
      console.error("[resume] iframe 自适应绑定失败", error);
    }
  }
  // 每次导航/重载 iframe 都重新绑定（旧 observer 随旧 document 失效）
  frame.addEventListener("load", bind);
})();
</script>
