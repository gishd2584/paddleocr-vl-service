/* PaddleOCR-VL 文档解析 - 前端逻辑 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const dropzone = $("dropzone");
  const fileInput = $("fileInput");
  const overlay = $("overlay");
  const overlayText = $("overlayText");

  let currentResult = null;
  let pollToken = 0; // 每次新上传自增，旧轮询自动作废

  // ---------- 启动：读取服务状态 ----------
  async function loadHealth() {
    try {
      const r = await fetch("/api/health");
      const j = await r.json();
      $("meta").textContent = `device: ${j.device} · 版本: ${j.pipeline_version}`;
      if (j.device) {
        const opt = [...$("device").options].find((o) => o.value === j.device);
        if (opt) $("device").value = j.device;
      }
    } catch (e) {
      $("meta").textContent = "无法连接服务";
    }
  }

  // ---------- 拖拽 / 选择 ----------
  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) handleFile(e.target.files[0]);
  });
  ["dragenter", "dragover"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      dropzone.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      dropzone.classList.remove("drag");
    })
  );
  dropzone.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) handleFile(f);
  });

  // ---------- 高级选项 ----------
  $("advToggle").addEventListener("click", () => {
    const b = $("advBody");
    b.hidden = !b.hidden;
    $("advToggle").textContent = b.hidden ? "高级选项 ▾" : "高级选项 ▴";
  });

  // ---------- Tabs ----------
  $("tabs").addEventListener("click", (e) => {
    const t = e.target.closest(".tab");
    if (!t) return;
    [...$("tabs").children].forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    const name = t.dataset.tab;
    ["preview", "md", "json"].forEach((n) => {
      $("pane-" + n).classList.toggle("active", n === name);
    });
    if (name === "preview" && currentResult) renderMath();
  });

  // ---------- 处理文件 ----------
  async function handleFile(file) {
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    const allowed = ["png", "jpg", "jpeg", "webp", "bmp", "pdf"];
    if (!allowed.includes(ext)) {
      alert("不支持的文件类型: ." + ext);
      return;
    }

    const previewCard = $("previewCard");
    const previewBody = $("previewBody");
    previewCard.hidden = false;
    $("fileName").textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB`;
    const objUrl = URL.createObjectURL(file);
    if (ext === "pdf") {
      previewBody.innerHTML = `<iframe src="${objUrl}#toolbar=1&view=FitH"></iframe>`;
    } else {
      previewBody.innerHTML = `<img src="${objUrl}" alt="preview" />`;
    }

    // 重置结果区
    currentResult = null;
    $("mdRender").innerHTML = "";
    $("mdRaw").textContent = "";
    $("jsonRaw").textContent = "";
    $("phPreview").hidden = false;
    $("dlMd").disabled = true;
    $("dlJson").disabled = true;
    $("filesFoot").hidden = true;

    await parse(file);
  }

  // ---------- 提交解析（立即返回 job_id，随后轮询状态）----------
  async function parse(file) {
    overlay.hidden = false;
    overlayText.textContent = "上传中…";
    try {
      const fd = new FormData();
      fd.append("file", file);
      const qs = new URLSearchParams({
        device: $("device").value,
        pipeline_version: $("version").value,
        use_orient: String($("orient").checked),
        use_unwarp: String($("unwarp").checked),
        use_layout: String($("layout").checked),
      });
      const resp = await fetch("/api/parse?" + qs.toString(), { method: "POST", body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const init = await resp.json();
      pollStatus(init.job_id);
    } catch (e) {
      overlay.hidden = true;
      $("phPreview").hidden = false;
      $("mdRender").innerHTML = `<p style="color:#e5484d">提交失败：${escapeHtml(e.message)}</p>`;
    }
  }

  // ---------- 轮询任务状态（避免长连接被代理掐断）----------
  function pollStatus(job_id) {
    const myToken = ++pollToken;
    async function tick() {
      if (myToken !== pollToken) return; // 已有更新的上传，停止旧轮询
      try {
        const r = await fetch("/api/status/" + job_id);
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${r.status}`);
        }
        const st = await r.json();
        overlayText.textContent = st.message || "解析中…";
        if (st.status === "done") {
          overlay.hidden = true;
          currentResult = st.result;
          renderResult(st.result);
          return;
        }
        if (st.status === "error") {
          overlay.hidden = true;
          $("phPreview").hidden = false;
          $("mdRender").innerHTML = `<p style="color:#e5484d">解析失败：${escapeHtml(st.error || st.message || "未知错误")}</p>`;
          return;
        }
        if (st.status === "pending") overlayText.textContent = "任务排队中（等待 GPU）…";
        setTimeout(tick, 1500);
      } catch (e) {
        overlay.hidden = true;
        $("phPreview").hidden = false;
        $("mdRender").innerHTML = `<p style="color:#e5484d">状态查询失败：${escapeHtml(e.message)}</p>`;
      }
    }
    tick();
  }

  // ---------- 渲染结果 ----------
  function renderResult(data) {
    $("phPreview").hidden = true;

    const base = data.results_base || "";
    const fixed = fixImageUrls(data.markdown || "", base);
    if (window.marked) {
      $("mdRender").innerHTML = window.marked.parse(fixed);
    } else {
      $("mdRender").textContent = data.markdown || "";
    }
    renderMath();

    $("mdRaw").textContent = data.markdown || "";

    const jp = data.json_pages || [];
    let jsonText = "";
    for (const j of jp) {
      try {
        jsonText += JSON.stringify(JSON.parse(j), null, 2) + "\n";
      } catch {
        jsonText += j + "\n";
      }
    }
    $("jsonRaw").textContent = jsonText || "(无 JSON 输出)";

    $("dlMd").disabled = false;
    $("dlJson").disabled = jp.length === 0;
    $("dlMd").onclick = () => download(data.markdown || "", stem(data.filename) + ".md", "text/markdown");
    if (jp.length) {
      let blobContent = jsonText;
      try {
        blobContent = JSON.stringify(jp.map((x) => JSON.parse(x)), null, 2);
      } catch {}
      $("dlJson").onclick = () => download(blobContent, stem(data.filename) + ".json", "application/json");
    }

    const links = (data.files || [])
      .map((f) => `<a href="${base}/${f}" target="_blank" rel="noopener">${f}</a>`)
      .join("");
    $("fileLinks").innerHTML = links;
    $("filesFoot").hidden = !links;
  }

  function renderMath() {
    if (window.renderMathInElement) {
      try {
        window.renderMathInElement($("mdRender"), {
          delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "$", right: "$", display: false },
            { left: "\\(", right: "\\)", display: false },
            { left: "\\[", right: "\\]", display: true },
          ],
          throwOnError: false,
        });
      } catch (e) {}
    }
  }

  // 把 markdown 里的相对图片路径改写为 /results/{job_id}/...
  function fixImageUrls(md, base) {
    if (!base) return md;
    return md.replace(/!\[[^\]]*\]\(([^)\s]+)\)/g, (m, url) => {
      if (/^(https?:|\/|data:)/.test(url)) return m;
      const u = url.replace(/^\.\//, "").replace(/^\.\.\//, "");
      return m.replace(url, base + "/" + u);
    });
  }

  function download(text, name, type) {
    const blob = new Blob([text], { type: type + ";charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function stem(name) {
    return (name || "output").replace(/\.[^.]+$/, "") || "output";
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  loadHealth();
})();
