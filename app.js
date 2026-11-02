document.addEventListener('DOMContentLoaded', () => {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  const menuToggle = document.getElementById('menuToggle');
  const closeSidebar = () => {
    if (sidebar) sidebar.classList.remove('open');
    if (overlay) overlay.classList.remove('show');
  };

  if (menuToggle && sidebar && overlay) {
    menuToggle.addEventListener('click', () => {
      sidebar.classList.toggle('open');
      overlay.classList.toggle('show');
    });
    overlay.addEventListener('click', closeSidebar);
  }

  document.querySelectorAll('[data-toggle-password]').forEach((button) => {
    button.addEventListener('click', () => {
      const input = document.getElementById(button.dataset.togglePassword);
      if (!input) return;
      const shouldShow = input.type === 'password';
      input.type = shouldShow ? 'text' : 'password';
      button.innerHTML = shouldShow ? '<i class="bi bi-eye-slash"></i>' : '<i class="bi bi-eye"></i>';
      button.setAttribute('aria-label', shouldShow ? 'Hide password' : 'Show password');
    });
  });

  document.querySelectorAll('[data-copy]').forEach((button) => {
    button.addEventListener('click', async () => {
      const value = button.dataset.copy || '';
      const label = button.querySelector('span');
      try {
        await navigator.clipboard.writeText(value);
        if (label) label.textContent = 'Copied!';
        else button.innerHTML = '<i class="bi bi-check2"></i> Copied!';
      } catch (_error) {
        const temporary = document.createElement('textarea');
        temporary.value = value;
        document.body.appendChild(temporary);
        temporary.select();
        document.execCommand('copy');
        temporary.remove();
        if (label) label.textContent = 'Copied!';
      }
      window.setTimeout(() => {
        if (label) label.textContent = 'Copy code';
        else button.innerHTML = '<i class="bi bi-copy"></i> Copy code';
      }, 1800);
    });
  });

  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (!window.confirm(form.dataset.confirm || 'Are you sure?')) event.preventDefault();
    });
  });

  // Convert the invite code to uppercase while the user types.
  const inviteInput = document.getElementById('invite_code');
  if (inviteInput) {
    inviteInput.addEventListener('input', () => {
      const start = inviteInput.selectionStart;
      inviteInput.value = inviteInput.value.toUpperCase();
      inviteInput.setSelectionRange(start, start);
    });
  }
});
