// Utility functions
const utils = {
    // Экранирование пользовательского текста перед вставкой в innerHTML.
    // Все поля, пришедшие от Telegram-пользователей (имя, username, текст
    // заявок/сообщений, ссылки на каналы и т.п.), считаются недоверенными —
    // без этого злоумышленник мог бы выполнить произвольный JS в сессии
    // администратора (украсть admin_token, дёргать API от его имени).
    escapeHtml: (text) => {
        const div = document.createElement('div');
        div.textContent = text === null || text === undefined ? '' : String(text);
        return div.innerHTML;
    },

    showLoading: (element) => {
        element.innerHTML = '<span class="loading"></span> Загрузка...';
        element.disabled = true;
    },
    
    hideLoading: (element, text) => {
        element.innerHTML = text;
        element.disabled = false;
    },
    
    showAlert: (message, type = 'success') => {
        const alert = document.createElement('div');
        alert.className = `alert alert-${type}`;
        alert.textContent = message;
        document.querySelector('.content-card').prepend(alert);
        setTimeout(() => alert.remove(), 5000);
    },
    
    formatDate: (dateString) => {
        const date = new Date(dateString);
        return date.toLocaleString('ru-RU');
    },
    
    formatCurrency: (amount) => {
        return `$${parseFloat(amount).toFixed(2)}`;
    }
};

// Modal functions
const modal = {
    show: (modalId) => {
        const overlay = document.getElementById(modalId);
        if (overlay) {
            overlay.classList.add('show');
            document.body.style.overflow = 'hidden';
        }
    },
    
    hide: (modalId) => {
        const overlay = document.getElementById(modalId);
        if (overlay) {
            overlay.classList.remove('show');
            document.body.style.overflow = '';
        }
    },
    
    init: () => {
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('modal-overlay')) {
                modal.hide(e.target.id);
            }
        });
    }
};

// API functions
const api = {
    getToken: () => {
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            const [name, value] = cookie.trim().split('=');
            if (name === 'admin_token') {
                return value;
            }
        }
        return null;
    },
    
    request: async (url, options = {}) => {
        const token = api.getToken();
        if (!options.headers) options.headers = {};
        if (token) {
            options.headers['Authorization'] = `Bearer ${token}`;
        }
        // Кастомный заголовок — простая CSRF-защита: его не может выставить
        // обычная HTML-форма с другого сайта, только JS с того же origin
        // (cross-origin fetch с этим заголовком упрётся в CORS).
        options.headers['X-Requested-With'] = 'XMLHttpRequest';
        options.credentials = 'include';
        
        const response = await fetch(url, options);
        const contentType = response.headers.get('content-type');
        
        if (contentType && contentType.includes('application/json')) {
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.detail || 'Ошибка запроса');
            }
            return data;
        }
        
        return response;
    }
};

// Бэйджи-уведомления (как непрочитанное в мессенджерах): счётчики новых заявок
// в сайдбаре + счётчики непрочитанных сообщений на кнопках "Переписка".
const notifications = {
    unreadChats: {},

    async fetchSummary() {
        try {
            const data = await api.request('/api/notifications/summary');
            notifications.unreadChats = data.unread_chats || {};
            notifications.setBadge('nav-badge-withdrawals', data.withdrawals_pending);
            notifications.setBadge('nav-badge-tickets', data.tickets_open);
            notifications.setBadge('nav-badge-appeals', data.appeals_pending);
            notifications.setBadge('nav-badge-ad-requests', data.ad_requests_pending);
            notifications.applyChatBadges();
        } catch (e) {
            // Тихо игнорируем (например, если сессия ещё не авторизована на этой странице)
        }
    },

    setBadge(elementId, count) {
        const el = document.getElementById(elementId);
        if (!el) return;
        if (count > 0) {
            el.textContent = count > 99 ? '99+' : count;
            el.style.display = 'inline-flex';
        } else {
            el.style.display = 'none';
        }
    },

    // Обновляет все бэйджи непрочитанных сообщений на текущей странице —
    // вызывать после api-опроса и после перерисовки любого списка заявок.
    applyChatBadges() {
        document.querySelectorAll('[data-chat-badge]').forEach((el) => {
            const count = notifications.unreadChats[el.getAttribute('data-chat-badge')] || 0;
            if (count > 0) {
                el.textContent = count > 99 ? '99+' : count;
                el.style.display = 'inline-flex';
            } else {
                el.style.display = 'none';
            }
        });
    },

    start() {
        notifications.fetchSummary();
        setInterval(notifications.fetchSummary, 5000);

        // Кнопка "Назад" в браузере иногда восстанавливает страницу из bfcache —
        // со старым, замороженным DOM и приостановленными таймерами. Без этого
        // бэйджи могли показывать устаревшие цифры, пока не тикнет следующий
        // опрос (или вообще не тикнет, если вкладка была в фоне).
        window.addEventListener('pageshow', (event) => {
            if (event.persisted) {
                notifications.fetchSummary();
            }
        });
    },
};

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    modal.init();
    notifications.start();
    
    // Add smooth scroll
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                target.scrollIntoView({ behavior: 'smooth' });
            }
        });
    });
    
    // Add fade-in animation to cards
    const cards = document.querySelectorAll('.content-card, .stat-card');
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.style.animation = 'fadeIn 0.5s ease';
            }
        });
    });
    
    cards.forEach(card => observer.observe(card));
});


