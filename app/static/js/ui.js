/* ============================================================
   UI Utilities - Interatividade profissional do painel
   ============================================================ */

document.addEventListener("DOMContentLoaded", function () {
  initializeSidebar();
  setupPopovers();
  setupDropdowns();
  setupToastContainer();

  setupReviewBackButtons();
  setupFailedExtractionDeletion();
});

/* ============================================================
   SIDEBAR
   ============================================================ */

function initializeSidebar() {
  const sidebarToggle = document.querySelector(".header-mobile-toggle");
  const sidebar = document.querySelector(".sidebar");
  const desktop = window.matchMedia("(min-width: 1025px)");
  const storageKey = "admin-sidebar-collapsed";

  if (sidebarToggle && sidebar) {
    const updateToggleState = () => {
      if (desktop.matches) {
        const collapsed = localStorage.getItem(storageKey) === "true";
        document.body.classList.toggle("sidebar-collapsed", collapsed);
        sidebar.classList.remove("open");
        sidebarToggle.setAttribute("aria-expanded", String(!collapsed));
        sidebarToggle.setAttribute("aria-label", collapsed ? "Expandir menu lateral" : "Recolher menu lateral");
      } else {
        const open = sidebar.classList.contains("open");
        sidebarToggle.setAttribute("aria-expanded", String(open));
        sidebarToggle.setAttribute("aria-label", open ? "Fechar menu lateral" : "Abrir menu lateral");
      }
    };

    updateToggleState();

    sidebarToggle.addEventListener("click", function () {
      if (desktop.matches) {
        localStorage.setItem(storageKey, String(!document.body.classList.contains("sidebar-collapsed")));
      } else {
        sidebar.classList.toggle("open");
      }
      updateToggleState();
    });

    desktop.addEventListener("change", updateToggleState);

    // Fechar sidebar ao clicar em um link
    document.querySelectorAll(".sidebar-nav-link").forEach((link) => {
      link.addEventListener("click", function () {
        if (!desktop.matches) {
          sidebar.classList.remove("open");
          updateToggleState();
        }
      });
    });

    // Fechar sidebar ao clicar fora
    document.addEventListener("click", function (e) {
      if (!desktop.matches && !sidebar.contains(e.target) && !sidebarToggle.contains(e.target)) {
        sidebar.classList.remove("open");
        updateToggleState();
      }
    });
  }

  // Marcar link ativo
  const currentPath = window.location.pathname;
  document.querySelectorAll(".sidebar-nav-link").forEach((link) => {
    const href = link.getAttribute("href");
    if (href && (href === currentPath || (currentPath.startsWith(href) && href !== "/"))) {
      link.classList.add("active");
    }
  });
}

/* ============================================================
   POPOVERS
   ============================================================ */

function setupPopovers() {
  document.querySelectorAll("[popover]").forEach((popover) => {
    const triggers = document.querySelectorAll(`[popovertarget="${popover.id}"]`);
    triggers.forEach((trigger) => {
      trigger.addEventListener("click", (e) => {
        e.preventDefault();
        if (popover.matches(":popover-open")) {
          popover.hidePopover();
        } else {
          popover.showPopover();
        }
      });
    });
  });
}

/* ============================================================
   DROPDOWNS
   ============================================================ */

function setupDropdowns() {
  document.querySelectorAll(".dropdown-trigger").forEach((trigger) => {
    trigger.addEventListener("click", function (e) {
      e.preventDefault();
      const menu = this.nextElementSibling;
      if (menu && menu.classList.contains("dropdown-menu")) {
        menu.classList.toggle("open");
      }
    });
  });

  // Fechar dropdown ao clicar fora
  document.addEventListener("click", function (e) {
    if (!e.target.closest(".dropdown-trigger")) {
      document.querySelectorAll(".dropdown-menu.open").forEach((menu) => {
        menu.classList.remove("open");
      });
    }
  });

  // Fechar dropdown ao pressionar Escape
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      document.querySelectorAll(".dropdown-menu.open").forEach((menu) => {
        menu.classList.remove("open");
      });
    }
  });
}

/* ============================================================
   TOAST CONTAINER
   ============================================================ */

function setupToastContainer() {
  if (!document.getElementById("toast-container")) {
    const container = document.createElement("div");
    container.id = "toast-container";
    container.style.cssText = `
      position: fixed;
      top: 20px;
      right: 20px;
      z-index: var(--z-toast);
      display: flex;
      flex-direction: column;
      gap: 8px;
      pointer-events: none;
    `;
    document.body.appendChild(container);
  }
}

/* ============================================================
   TOAST NOTIFICATIONS
   ============================================================ */

function showToast(message, type = "info", duration = 3000) {
  const container = document.getElementById("toast-container") || setupToastContainer();

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.style.cssText = `
    background: ${getToastColor(type)};
    color: white;
    padding: 12px 16px;
    border-radius: 6px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    animation: slideInDown 0.3s ease-out;
    pointer-events: auto;
    cursor: pointer;
    max-width: 400px;
    word-wrap: break-word;
  `;

  toast.textContent = message;
  document.getElementById("toast-container").appendChild(toast);

  const autoClose = () => {
    toast.style.animation = "slideInUp 0.3s ease-out";
    setTimeout(() => toast.remove(), 300);
  };

  toast.addEventListener("click", autoClose);
  setTimeout(autoClose, duration);

  return toast;
}

function getToastColor(type) {
  const colors = {
    success: "var(--color-success)",
    error: "var(--color-error)",
    warning: "var(--color-warning)",
    info: "var(--color-info)",
    processing: "var(--color-processing)",
  };
  return colors[type] || colors.info;
}

/* ============================================================
   CONFIRMAÇÃO
   ============================================================ */

function confirmAction(message, callback, confirmText = "Confirmar", cancelText = "Cancelar") {
  return new Promise((resolve) => {
    if (window.confirm(message)) {
      callback();
      resolve(true);
    } else {
      resolve(false);
    }
  });
}

/* ============================================================
   FORMATADORES
   ============================================================ */

function formatDate(dateString, showTime = false) {
  if (!dateString) return "-";
  try {
    const date = new Date(dateString);
    const options = {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      ...(showTime && {
        hour: "2-digit",
        minute: "2-digit",
      }),
    };
    return date.toLocaleDateString("pt-BR", options);
  } catch {
    return dateString;
  }
}

function formatDateTime(dateString) {
  return formatDate(dateString, true);
}

function formatCPF(cpf) {
  if (!cpf) return "";
  const clean = cpf.replace(/\D/g, "");
  return clean.replace(/(\d{3})(\d{3})(\d{3})(\d{2})/, "$1.$2.$3-$4");
}

function maskCPF(cpf) {
  if (!cpf) return "";
  return cpf.replace(/\d(?=\d{2})/g, "*");
}

function formatPhoneNumber(phone) {
  if (!phone) return "";
  const clean = phone.replace(/\D/g, "");
  if (clean.length === 11) {
    return clean.replace(/(\d{2})(\d{5})(\d{4})/, "($1) $2-$3");
  } else if (clean.length === 10) {
    return clean.replace(/(\d{2})(\d{4})(\d{4})/, "($1) $2-$3");
  }
  return phone;
}

/* ============================================================
   UTILIDADES DOM
   ============================================================ */

function showElement(element) {
  if (element) {
    element.classList.remove("hidden");
    element.classList.add("visible");
  }
}

function hideElement(element) {
  if (element) {
    element.classList.add("hidden");
    element.classList.remove("visible");
  }
}

function toggleElement(element) {
  if (element) {
    element.classList.toggle("hidden");
    element.classList.toggle("visible");
  }
}

/* ============================================================
   STATUS BADGES
   ============================================================ */

function getStatusBadgeClass(status) {
  const statusMap = {
    pendente: "badge-pending",
    processando: "badge-processing",
    confirmado: "badge-success",
    rejeitado: "badge-error",
    revisado: "badge-info",
    ativo: "badge-success",
    inativo: "badge-neutral",
  };
  return statusMap[status] || "badge-neutral";
}

function getStatusLabel(status) {
  const labelMap = {
    pendente: "Pendente",
    processando: "Processando",
    processando_manual: "Processando (manual)",
    confirmado: "Confirmado",
    rejeitado: "Rejeitado",
    revisado: "Revisado",
    ativo: "Ativo",
    inativo: "Inativo",
  };
  return labelMap[status] || status;
}

function getStatusIcon(status) {
  return "";
}

/* ============================================================
   LOADING STATE
   ============================================================ */

function showLoading(element) {
  if (element) {
    element.classList.add("loading");
    const spinner = document.createElement("div");
    spinner.className = "loading-spinner";
    element.appendChild(spinner);
  }
}

function hideLoading(element) {
  if (element) {
    element.classList.remove("loading");
    const spinner = element.querySelector(".loading-spinner");
    if (spinner) spinner.remove();
  }
}

/* ============================================================
   REVIEW - VOLTAR
   ============================================================ */

function setupReviewBackButtons() {
  const buttons = document.querySelectorAll("[data-review-back]");

  buttons.forEach((button) => {
    button.addEventListener("click", function () {
      const fallbackUrl =
        button.dataset.fallbackUrl || "/";

      const referrer = document.referrer;

      if (referrer) {
        try {
          const previousUrl = new URL(referrer);

          /*
           * Só utiliza history.back() se a página anterior
           * pertence ao próprio sistema.
           *
           * Evita mandar o usuário de volta para outro site.
           */
          if (previousUrl.origin === window.location.origin) {
            window.history.back();
            return;
          }
        } catch (error) {
          console.warn(
            "Não foi possível identificar a página anterior.",
            error
          );
        }
      }

      window.location.href = fallbackUrl;
    });
  });
}


/* ============================================================
   EXTRAÇÕES COM FALHA - EXCLUSÃO
   ============================================================ */

function setupFailedExtractionDeletion() {
  const modal = document.getElementById(
    "failed-extraction-delete-modal"
  );

  if (!modal) {
    return;
  }

  const triggers = document.querySelectorAll(
    ".failed-extraction-delete-trigger"
  );

  const form = document.getElementById(
    "failed-extraction-delete-form"
  );

  const fileNameElement = document.getElementById(
    "failed-delete-file-name"
  );

  const cancelButton = document.getElementById(
    "failed-extraction-delete-cancel"
  );

  const confirmButton = form?.querySelector(
    'button[type="submit"]'
  );

  let lastTrigger = null;


  function openDeleteModal(trigger) {
    const action = trigger.dataset.deleteAction;
    const fileName =
      trigger.dataset.fileName || "este arquivo";

    if (!action || !form) {
      return;
    }

    lastTrigger = trigger;

    form.action = action;

    if (fileNameElement) {
      fileNameElement.textContent = `"${fileName}"`;
    }

    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");

    document.body.classList.add("modal-open");

    window.requestAnimationFrame(() => {
      cancelButton?.focus();
    });
  }


  function closeDeleteModal() {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");

    document.body.classList.remove("modal-open");

    if (form) {
      form.action = "";
    }

    lastTrigger?.focus();
    lastTrigger = null;
  }


  triggers.forEach((trigger) => {
    trigger.addEventListener("click", function () {
      openDeleteModal(trigger);
    });
  });


  cancelButton?.addEventListener("click", function () {
    closeDeleteModal();
  });


  modal.addEventListener("click", function (event) {
    if (event.target === modal) {
      closeDeleteModal();
    }
  });


  document.addEventListener("keydown", function (event) {
    if (
      event.key === "Escape" &&
      modal.classList.contains("open")
    ) {
      closeDeleteModal();
    }
  });


  form?.addEventListener("submit", function () {
    if (confirmButton) {
      confirmButton.disabled = true;
      confirmButton.textContent = "Excluindo...";
    }
  });
}

/* ============================================================
   EXPORT
   ============================================================ */

window.UI = {
  showToast,
  confirmAction,
  formatDate,
  formatDateTime,
  formatCPF,
  maskCPF,
  formatPhoneNumber,
  showElement,
  hideElement,
  toggleElement,
  getStatusBadgeClass,
  getStatusLabel,
  getStatusIcon,
  showLoading,
  hideLoading,
};
