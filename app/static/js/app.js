// ---------- Global page-loading indicator ----------
// A thin progress bar across the very top of the viewport (the same
// pattern GitHub/YouTube/Linear use) so that clicking anything that
// navigates away — a link, a form submit, a clickable table row, a
// calendar event — gives instant visible feedback instead of the page
// silently sitting there while a slow server response or a normal full
// reload is in flight.
//
// It hooks the browser's own "beforeunload" signal rather than trying to
// separately intercept every click/submit pattern in this app (plain
// links, POST forms, `onclick="window.location=...'` table rows, the
// calendar's own event-click handler, browser back/forward, a manual
// refresh...). beforeunload fires for all of them uniformly, and — just
// as usefully — it does NOT fire for anything that *doesn't* actually
// leave this page: a target="_blank" link, a mailto:/tel: link, a
// download, a cancelled confirm() on a form, a Bootstrap tab/modal/
// dropdown toggle. The browser already knows the exact moment a real
// navigation is happening; this just reacts to it instead of guessing.
//
// Nothing here ever returns a value or touches event.returnValue from the
// beforeunload handler — doing either is what triggers the native "leave
// this page?" browser prompt, which is never the intent here.
(function () {
  var bar = document.createElement("div");
  bar.id = "page-loader-bar";
  document.documentElement.appendChild(bar);

  var progress = 0;
  var timer = null;

  function start() {
    if (timer) return; // already climbing — a page can only unload once
    progress = 0;
    bar.classList.remove("done");
    bar.style.width = "0%";
    void bar.offsetWidth; // force a reflow so the width reset above lands before animating
    bar.classList.add("active");
    timer = setInterval(function () {
      // Decelerating climb toward (never reaching) 90% — there's nothing
      // in this architecture that knows real load progress, so this just
      // keeps visibly moving for as long as the current page is still
      // here, however long that turns out to be.
      var remaining = 90 - progress;
      progress += Math.max(0.5, remaining * 0.06);
      bar.style.width = Math.min(progress, 90) + "%";
    }, 150);
  }

  window.addEventListener("beforeunload", start);

  // Chrome/Firefox can restore a page from the back/forward cache instead
  // of reloading it — that resumes this exact script instance, bar and
  // all, possibly still mid-climb from right before the user navigated
  // away. Reset it so arriving via Back never shows a stuck bar.
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    clearInterval(timer);
    timer = null;
    bar.classList.remove("active", "done");
    bar.style.width = "0%";
  });
})();

document.addEventListener("DOMContentLoaded", function () {
  const sidebar = document.querySelector(".app-sidebar");
  const toggleBtn = document.querySelector(".sidebar-toggle");

  if (toggleBtn && sidebar) {
    toggleBtn.addEventListener("click", function () {
      if (window.innerWidth <= 900) {
        sidebar.classList.toggle("mobile-open");
      } else {
        sidebar.classList.toggle("collapsed");
      }
    });
  }

  document.querySelectorAll(".nav-item.disabled").forEach(function (el) {
    el.addEventListener("click", function (e) {
      e.preventDefault();
    });
  });

  const toastEls = document.querySelectorAll(".toast");
  toastEls.forEach(function (el) {
    const toast = new bootstrap.Toast(el, { delay: 4500 });
    toast.show();
  });

  // Restore whichever Bootstrap tab a form redirected back to (e.g. after
  // adding a Note, the server redirects to .../workspace/123#tab-notes so
  // the page lands back on Notes instead of resetting to Overview).
  if (window.location.hash) {
    const pane = document.querySelector(window.location.hash);
    if (pane && pane.classList.contains("tab-pane")) {
      const trigger = document.querySelector('[data-bs-target="' + window.location.hash + '"]');
      if (trigger) {
        new bootstrap.Tab(trigger).show();
        // A full page reload resets scroll to the top regardless of the
        // URL fragment, since the target pane is display:none at initial
        // paint (before this JS runs) — the browser's automatic
        // anchor-scroll silently no-ops on a hidden element. Bring the
        // tab bar (and whatever just got added below it) back into view
        // now that the right tab is actually showing, so the page doesn't
        // feel like it "jumped to the top" after every action.
        trigger.scrollIntoView({ block: "start" });
      }
    }
  }
});

// ---------- Corporate Calendar: create/edit event form ----------
// Wires the conditional field toggles (Event Type "Other", Budget amount,
// Conflicts notes) and intercepts submit/delete/cancel so the exact same
// server-rendered fragment (app/templates/corporate_calendar/
// _event_form_fields.html) works two ways with no duplication: as the
// whole page on a direct visit to .../create or .../<id>/edit (see
// event_form.html), and injected into a modal on top of the calendar grid
// (see mine.html) via fetch. `container` is whichever element currently
// holds the fragment; `options.onSaved`/`onDeleted`/`onCancel` are how the
// caller decides what "done" means in its context (close the modal and
// refresh the grid, vs. redirect back to the calendar page).
//
// The server signals success with an empty 204 response and a validation
// failure by re-rendering the same fragment (now carrying field/service
// errors) with a 200 — so a submit failure just swaps the container's
// content in place and re-wires it, exactly like the first load did.
function wireCorporateEventForm(container, options) {
  options = options || {};
  const form = container.querySelector("form[data-corporate-event-form]");
  if (!form) return;

  const eventTypeSelect = form.querySelector("#event_type");
  const eventTypeOtherRow = form.querySelector("#event_type_other_row");
  function syncEventTypeOther() {
    eventTypeOtherRow.classList.toggle("d-none", eventTypeSelect.value !== "OTHER");
  }
  eventTypeSelect.addEventListener("change", syncEventTypeOther);
  syncEventTypeOther();

  const budgetAmountRow = form.querySelector("#budget_amount_row");
  function syncBudget() {
    const checked = form.querySelector('input[name="budget_involved"]:checked');
    budgetAmountRow.classList.toggle("d-none", !checked || checked.value !== "yes");
  }
  form.querySelectorAll('input[name="budget_involved"]').forEach(function (r) { r.addEventListener("change", syncBudget); });
  syncBudget();

  const conflictNotesRow = form.querySelector("#conflict_notes_row");
  function syncConflicts() {
    const checked = form.querySelector('input[name="conflicts_with_events"]:checked');
    conflictNotesRow.classList.toggle("d-none", !checked || checked.value !== "YES");
  }
  form.querySelectorAll('input[name="conflicts_with_events"]').forEach(function (r) { r.addEventListener("change", syncConflicts); });
  syncConflicts();

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    fetch(form.action, {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      body: new FormData(form),
    })
      .then(function (r) {
        if (r.status === 204) {
          if (options.onSaved) options.onSaved();
          return;
        }
        return r.text().then(function (html) {
          container.innerHTML = html;
          wireCorporateEventForm(container, options);
        });
      })
      .catch(function () { if (submitBtn) submitBtn.disabled = false; });
  });

  const cancelBtn = form.querySelector("#cancelEventBtn");
  if (cancelBtn) {
    cancelBtn.addEventListener("click", function () { if (options.onCancel) options.onCancel(); });
  }

  const deleteBtn = form.querySelector("#deleteEventBtn");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", function () {
      if (!confirm("Delete this event? This can't be undone.")) return;
      fetch(deleteBtn.dataset.deleteUrl, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": form.querySelector('input[name="csrf_token"]').value },
      }).then(function (r) {
        if (r.ok && options.onDeleted) options.onDeleted();
      });
    });
  }
}

// ---------- Calendar pages: "today's agenda" sidebar ----------
// Shared by app/templates/calendar/view.html and both Corporate Calendar
// pages — each colors its FullCalendar events via `classNames: ["fc-event-
// pill", "tag-<name>"]` (see corporate_calendar_service.event_to_calendar_dict
// and calendar.routes.events_json), so the accent color for an agenda card
// can be read straight back off the event instead of needing a second,
// page-specific color lookup here.

function eventsOverlappingToday(calendarApi) {
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const todayEnd = new Date(todayStart.getTime() + 24 * 60 * 60 * 1000);
  return calendarApi.getEvents().filter(function (e) {
    const start = e.start;
    const end = e.end || e.start;
    return start && start < todayEnd && end > todayStart;
  });
}

function renderCalendarAgenda(container, events, options) {
  options = options || {};
  events = events.slice().sort(function (a, b) { return a.start - b.start; });

  if (!events.length) {
    container.innerHTML = '<div class="cal-agenda-empty">Nothing scheduled for today.</div>';
    return;
  }

  container.innerHTML = "";
  events.forEach(function (event) {
    const colorClass = (event.classNames || []).find(function (c) { return c.indexOf("tag-") === 0; }) || "tag-violet";

    let timeLabel;
    if (event.allDay) {
      timeLabel = "All day";
    } else {
      const fmt = function (d) { return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }); };
      timeLabel = event.end ? fmt(event.start) + " – " + fmt(event.end) : fmt(event.start);
    }

    const card = document.createElement("div");
    card.className = "cal-agenda-card " + colorClass.replace("tag-", "tag-accent-");
    card.innerHTML = '<div class="cal-agenda-time"></div><div class="cal-agenda-title"></div><div class="cal-agenda-meta"></div>';
    card.querySelector(".cal-agenda-time").textContent = timeLabel;
    card.querySelector(".cal-agenda-title").textContent = event.title;
    const metaText = options.getMeta ? options.getMeta(event) : "";
    const metaEl = card.querySelector(".cal-agenda-meta");
    if (metaText) {
      metaEl.textContent = metaText;
    } else {
      metaEl.remove();
    }
    if (options.onClick) card.addEventListener("click", function () { options.onClick(event); });
    container.appendChild(card);
  });
}
