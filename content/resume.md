---
title: 简历
---

<iframe id="resume-frame" src="/_resume/" title="简历" scrolling="no"
  style="width:100%;border:1px solid #ddd;border-radius:4px;display:block"></iframe>

<script>
// 同源 iframe：读取源页真实高度并回填，使其随内容自适应（含窗口缩放、断点切换）
(function () {
  var frame = document.getElementById("resume-frame");
  function fit() {
    try {
      // 先归零让内容塌缩，否则 scrollHeight 会被旧的 iframe 高度撑住（变宽不回缩）
      frame.style.height = "0";
      var doc = frame.contentDocument || frame.contentWindow.document;
      frame.style.height = doc.documentElement.scrollHeight + "px";
    } catch (error) {
      console.error("[resume] iframe 自适应失败", error);
    }
  }
  frame.addEventListener("load", fit);
  window.addEventListener("resize", fit);
})();
</script>
