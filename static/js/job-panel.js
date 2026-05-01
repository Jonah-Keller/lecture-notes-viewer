// Floating progress panel: polls /jobs/active every couple seconds and
// surfaces all in-flight + recently-completed generations regardless of
// which page the user is on. Hides itself when nothing is active.
(function () {
    const POLL_MS = 2000;
    const HIDE_DELAY_MS = 8000;  // keep panel visible briefly after queue empties

    const panel = document.getElementById('job-panel');
    if (!panel) return;
    const list = document.getElementById('job-panel-list');
    const countEl = document.getElementById('job-panel-count');
    const collapseBtn = document.getElementById('job-panel-collapse');

    let collapsed = localStorage.getItem('jobPanel.collapsed') === '1';
    applyCollapsed();
    collapseBtn.addEventListener('click', () => {
        collapsed = !collapsed;
        localStorage.setItem('jobPanel.collapsed', collapsed ? '1' : '0');
        applyCollapsed();
    });

    function applyCollapsed() {
        panel.classList.toggle('collapsed', collapsed);
        collapseBtn.textContent = collapsed ? '+' : '–';
        collapseBtn.title = collapsed ? 'Expand' : 'Collapse';
    }

    let lastNonEmptyAt = 0;

    async function poll() {
        try {
            const res = await fetch('/jobs/active', { cache: 'no-store' });
            if (!res.ok) return;
            const data = await res.json();
            renderList(data.jobs || []);
        } catch (err) {
            // network blip; will retry on next tick
        } finally {
            setTimeout(poll, POLL_MS);
        }
    }

    function stateLabel(j) {
        if (j.state === 'queued') return '⌛ queued';
        if (j.state === 'rendering') return '🖼 rendering slides';
        if (j.state === 'generating') return '✍ writing notes';
        if (j.state === 'writing') return '💾 saving';
        if (j.state === 'done') return '✅ ready';
        if (j.state === 'error') return '⚠ error';
        return j.state;
    }

    function elapsed(j) {
        const seconds = Math.max(0, Math.floor(Date.now() / 1000 - j.created_at));
        if (seconds < 60) return `${seconds}s`;
        const m = Math.floor(seconds / 60), s = seconds % 60;
        return `${m}m${s.toString().padStart(2, '0')}s`;
    }

    function escapeHtml(s) {
        return (s || '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        })[c]);
    }

    function renderList(jobs) {
        const active = jobs.filter(j =>
            ['queued', 'rendering', 'generating', 'writing'].includes(j.state)).length;
        const total = jobs.length;
        countEl.textContent = total > 0 ? `${active} active / ${total}` : '';

        if (total === 0) {
            // Hide after a short delay so the user can see "done" briefly.
            if (lastNonEmptyAt && (Date.now() - lastNonEmptyAt) > HIDE_DELAY_MS) {
                panel.classList.add('hidden');
            }
            list.innerHTML = '';
            return;
        }
        lastNonEmptyAt = Date.now();
        panel.classList.remove('hidden');

        const html = jobs.map(j => {
            const num = String(j.number).padStart(2, '0');
            const courseLabel = escapeHtml(j.course);
            const slug = escapeHtml(j.slug);
            const title = escapeHtml(j.title || j.slug);
            let action = '';
            if (j.state === 'done') {
                action = `<a href="/drafts/${courseLabel}/${slug}" class="job-row-link">review →</a>`;
            } else if (j.state === 'error') {
                action = `<span class="job-row-error">${escapeHtml(j.error)}</span>`;
            } else {
                const prog = j.progress ? `, ${escapeHtml(j.progress)}` : '';
                const slideInfo = j.slide_count ? `, ${j.slide_count} slides` : '';
                action = `<span class="job-row-progress">${prog.replace(/^,\s*/,'')}${slideInfo}</span>`;
            }
            return `
                <li class="job-row job-row-${escapeHtml(j.state)}">
                    <div class="job-row-line1">
                        <span class="job-row-num">${num}</span>
                        <span class="job-row-title" title="${title}">${title}</span>
                        <span class="job-row-elapsed">${elapsed(j)}</span>
                    </div>
                    <div class="job-row-line2">
                        <span class="job-row-course">${courseLabel}</span>
                        <span class="job-row-state">${stateLabel(j)}</span>
                    </div>
                    <div class="job-row-line3">${action}</div>
                </li>
            `;
        }).join('');
        list.innerHTML = html;
    }

    poll();
})();
