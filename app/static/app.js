(function () {
  'use strict';

  var UNLOCK_AFTER_MS = 15000; // если ответа нет слишком долго, разрешаем повторить (создание идемпотентно)

  // --- Защита от повторной отправки ---------------------------------------
  function controlsOf(form) {
    return Array.prototype.slice.call(form.querySelectorAll('button, select'));
  }

  function lock(form) {
    form.dataset.submitting = '1';
    form.setAttribute('aria-busy', 'true');
    // Блокируем уже после того, как браузер собрал данные формы (иначе disabled-поля не уйдут).
    setTimeout(function () {
      controlsOf(form).forEach(function (c) { c.disabled = true; });
      var btn = form.querySelector('[data-saving-text]');
      if (btn) {
        btn.dataset.originalText = btn.textContent;
        btn.textContent = btn.dataset.savingText;
      }
    }, 0);
    form._unlockTimer = setTimeout(function () { unlock(form); }, UNLOCK_AFTER_MS);
  }

  function unlock(form) {
    clearTimeout(form._unlockTimer);
    delete form.dataset.submitting;
    form.removeAttribute('aria-busy');
    controlsOf(form).forEach(function (c) { c.disabled = false; });
    var btn = form.querySelector('[data-saving-text]');
    if (btn && btn.dataset.originalText) {
      btn.textContent = btn.dataset.originalText;
    }
  }

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== 'post') return;
    if (form.dataset.submitting === '1') {
      event.preventDefault(); // повторное нажатие во время сохранения игнорируем
      return;
    }
    lock(form);
  });

  // Возврат кнопкой «Назад» берёт страницу из кэша вместе с заблокированными кнопками.
  window.addEventListener('pageshow', function (event) {
    if (!event.persisted) return;
    Array.prototype.forEach.call(document.querySelectorAll('form'), unlock);
  });

  // --- Смена статуса прямо в списке ---------------------------------------
  document.addEventListener('change', function (event) {
    var select = event.target;
    if (!select.matches || !select.matches('select[data-autosubmit], select[data-colorize]')) return;
    // Цвет статуса следует за выбранным значением (и в списке, и в форме редактирования)
    select.className = select.className.replace(/status-(new|in_progress|done)/, 'status-' + select.value);
    if (!select.hasAttribute('data-autosubmit')) return;
    var form = select.form;
    if (form.requestSubmit) form.requestSubmit(); else form.submit();
  });

  // --- Даты в часовом поясе пользователя ----------------------------------
  function localizeTimes() {
    Array.prototype.forEach.call(document.querySelectorAll('time[data-local]'), function (el) {
      var d = new Date(el.getAttribute('datetime'));
      if (isNaN(d.getTime())) return;
      el.textContent = d.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', localizeTimes);
  } else {
    localizeTimes();
  }
})();
