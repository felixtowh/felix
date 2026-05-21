/**
 * Dify Portal - Common JavaScript Utilities
 * 公共 JavaScript 工具函数
 */

// ==================== DOM 操作工具 ====================

/**
 * 等待 DOM 加载完成
 * @param {Function} callback - 回调函数
 */
function onReady(callback) {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', callback);
    } else {
        callback();
    }
}

/**
 * 选择单个元素
 * @param {string} selector - CSS 选择器
 * @param {Element} parent - 父元素（默认 document）
 * @returns {Element|null}
 */
function $(selector, parent = document) {
    return parent.querySelector(selector);
}

/**
 * 选择多个元素
 * @param {string} selector - CSS 选择器
 * @param {Element} parent - 父元素（默认 document）
 * @returns {NodeList}
 */
function $$(selector, parent = document) {
    return parent.querySelectorAll(selector);
}

// ==================== 模态框工具 ====================

/**
 * 打开模态框
 * @param {string} modalId - 模态框 ID
 */
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    }
}

/**
 * 关闭模态框
 * @param {string} modalId - 模态框 ID
 */
function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('active');
        document.body.style.overflow = '';
    }
}

/**
 * 关闭所有模态框
 */
function closeAllModals() {
    $$('.modal.active').forEach(modal => {
        modal.classList.remove('active');
    });
    document.body.style.overflow = '';
}

/**
 * 点击模态框外部关闭
 * @param {Event} event - 点击事件
 */
function handleModalOutsideClick(event) {
    if (event.target.classList.contains('modal')) {
        closeModal(event.target.id);
    }
}

// ==================== Toast 提示 ====================

/**
 * 显示 Toast 提示
 * @param {string} message - 消息内容
 * @param {number} duration - 显示时间（毫秒）
 */
function showToast(message, duration = 2000) {
    let toast = document.getElementById('toast');
    
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        toast.className = 'toast';
        document.body.appendChild(toast);
    }
    
    toast.textContent = message;
    toast.classList.add('show');
    
    setTimeout(() => {
        toast.classList.remove('show');
    }, duration);
}

// ==================== HTTP 请求工具 ====================

/**
 * 发送 HTTP 请求
 * @param {string} url - 请求地址
 * @param {Object} options - 请求选项
 * @returns {Promise}
 */
async function httpRequest(url, options = {}) {
    const defaultOptions = {
        method: 'GET',
        headers: {
            'Content-Type': 'application/json'
        }
    };
    
    const config = { ...defaultOptions, ...options };
    
    if (config.body && typeof config.body === 'object') {
        config.body = JSON.stringify(config.body);
    }
    
    try {
        const response = await fetch(url, config);
        
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || `HTTP error! status: ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('HTTP Request Error:', error);
        throw error;
    }
}

/**
 * GET 请求
 * @param {string} url - 请求地址
 * @returns {Promise}
 */
function get(url) {
    return httpRequest(url, { method: 'GET' });
}

/**
 * POST 请求
 * @param {string} url - 请求地址
 * @param {Object} data - 请求数据
 * @returns {Promise}
 */
function post(url, data) {
    return httpRequest(url, { method: 'POST', body: data });
}

/**
 * PUT 请求
 * @param {string} url - 请求地址
 * @param {Object} data - 请求数据
 * @returns {Promise}
 */
function put(url, data) {
    return httpRequest(url, { method: 'PUT', body: data });
}

/**
 * DELETE 请求
 * @param {string} url - 请求地址
 * @returns {Promise}
 */
function del(url) {
    return httpRequest(url, { method: 'DELETE' });
}

// ==================== 表单工具 ====================

/**
 * 获取表单数据
 * @param {HTMLFormElement} form - 表单元素
 * @returns {Object}
 */
function getFormData(form) {
    const formData = new FormData(form);
    const data = {};
    
    for (let [key, value] of formData.entries()) {
        if (data[key]) {
            if (!Array.isArray(data[key])) {
                data[key] = [data[key]];
            }
            data[key].push(value);
        } else {
            data[key] = value;
        }
    }
    
    return data;
}

/**
 * 验证表单
 * @param {HTMLFormElement} form - 表单元素
 * @returns {boolean}
 */
function validateForm(form) {
    const requiredFields = form.querySelectorAll('[required]');
    let isValid = true;
    
    requiredFields.forEach(field => {
        if (!field.value.trim()) {
            isValid = false;
            field.classList.add('error');
        } else {
            field.classList.remove('error');
        }
    });
    
    return isValid;
}

/**
 * 清空表单
 * @param {HTMLFormElement} form - 表单元素
 */
function clearForm(form) {
    form.reset();
    form.querySelectorAll('.error').forEach(field => {
        field.classList.remove('error');
    });
}

// ==================== 字符串工具 ====================

/**
 * 转义 HTML 特殊字符
 * @param {string} text - 原文本
 * @returns {string}
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 截断文本
 * @param {string} text - 原文本
 * @param {number} maxLength - 最大长度
 * @param {string} suffix - 后缀
 * @returns {string}
 */
function truncate(text, maxLength, suffix = '...') {
    if (!text || text.length <= maxLength) return text;
    return text.substring(0, maxLength) + suffix;
}

/**
 * 格式化日期
 * @param {Date|string|number} date - 日期
 * @param {string} format - 格式
 * @returns {string}
 */
function formatDate(date, format = 'YYYY-MM-DD') {
    const d = new Date(date);
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    const hour = String(d.getHours()).padStart(2, '0');
    const minute = String(d.getMinutes()).padStart(2, '0');
    const second = String(d.getSeconds()).padStart(2, '0');
    
    return format
        .replace('YYYY', year)
        .replace('MM', month)
        .replace('DD', day)
        .replace('HH', hour)
        .replace('mm', minute)
        .replace('ss', second);
}

// ==================== 存储工具 ====================

/**
 * 本地存储操作
 */
const storage = {
    /**
     * 设置本地存储
     * @param {string} key - 键
     * @param {any} value - 值
     */
    set(key, value) {
        localStorage.setItem(key, JSON.stringify(value));
    },
    
    /**
     * 获取本地存储
     * @param {string} key - 键
     * @param {any} defaultValue - 默认值
     * @returns {any}
     */
    get(key, defaultValue = null) {
        try {
            const item = localStorage.getItem(key);
            return item ? JSON.parse(item) : defaultValue;
        } catch {
            return defaultValue;
        }
    },
    
    /**
     * 删除本地存储
     * @param {string} key - 键
     */
    remove(key) {
        localStorage.removeItem(key);
    },
    
    /**
     * 清空本地存储
     */
    clear() {
        localStorage.clear();
    }
};

// ==================== 节流和防抖 ====================

/**
 * 节流函数
 * @param {Function} func - 原函数
 * @param {number} wait - 等待时间
 * @returns {Function}
 */
function throttle(func, wait) {
    let timeout = null;
    let previous = 0;
    
    return function(...args) {
        const now = Date.now();
        const remaining = wait - (now - previous);
        
        if (remaining <= 0 || remaining > wait) {
            if (timeout) {
                clearTimeout(timeout);
                timeout = null;
            }
            previous = now;
            func.apply(this, args);
        } else if (!timeout) {
            timeout = setTimeout(() => {
                previous = Date.now();
                timeout = null;
                func.apply(this, args);
            }, remaining);
        }
    };
}

/**
 * 防抖函数
 * @param {Function} func - 原函数
 * @param {number} wait - 等待时间
 * @returns {Function}
 */
function debounce(func, wait) {
    let timeout;
    
    return function(...args) {
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(this, args), wait);
    };
}

// ==================== 其他工具 ====================

/**
* 复制到剪贴板
* @param {string} text - 要复制的文本
* @returns {Promise<boolean>}
*/
async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (err) {
        // 降级方案
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.left = '-9999px';
        document.body.appendChild(textarea);
        textarea.select();
        
        try {
            document.execCommand('copy');
            return true;
        } catch (err) {
            console.error('Copy failed:', err);
            return false;
        } finally {
            document.body.removeChild(textarea);
        }
    }
}

/**
 * 下载文件
 * @param {Blob} blob - 文件 Blob
 * @param {string} filename - 文件名
 */
function downloadFile(blob, filename) {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
}

/**
 * 生成唯一 ID
 * @param {string} prefix - ID 前缀
 * @returns {string}
 */
function generateId(prefix = '') {
    return prefix + Date.now().toString(36) + Math.random().toString(36).substr(2);
}

// ==================== 初始化 ====================

onReady(() => {
    // 绑定模态框外部点击事件
    document.addEventListener('click', handleModalOutsideClick);
    
    // 绑定 ESC 键关闭模态框
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeAllModals();
        }
    });
});
