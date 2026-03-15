// app.js — shared utilities: theme, back-to-top, smooth animations

(function () {
  const THEME_KEY = "news-aggregator-theme";

  function getSystemTheme() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const moonIcon = document.querySelector("#themeToggle .moon-icon");
    const sunIcon = document.querySelector("#themeToggle .sun-icon");
    if (moonIcon && sunIcon) {
      if (theme === "dark") {
        moonIcon.style.display = "none";
        sunIcon.style.display = "block";
      } else {
        moonIcon.style.display = "block";
        sunIcon.style.display = "none";
      }
    }
  }

  // Initialize theme
  const savedTheme = localStorage.getItem(THEME_KEY);
  applyTheme(savedTheme || getSystemTheme());

  // Listen for system theme changes
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    if (!localStorage.getItem(THEME_KEY)) {
      applyTheme(e.matches ? "dark" : "light");
    }
  });

  // Theme toggle click handler
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("#themeToggle");
    if (!btn) return;
    const current = document.documentElement.getAttribute("data-theme") || getSystemTheme();
    const next = current === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  });

  // Back-to-top functionality
  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("backToTop");
    if (!btn) return;

    let ticking = false;
    window.addEventListener("scroll", () => {
      if (!ticking) {
        requestAnimationFrame(() => {
          btn.classList.toggle("visible", window.scrollY > 400);
          ticking = false;
        });
        ticking = true;
      }
    }, { passive: true });

    btn.addEventListener("click", () => {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  });

  // Header and search bar hide-on-scroll (like Twitter)
  document.addEventListener("DOMContentLoaded", () => {
    const topBar = document.querySelector(".top-bar");
    const searchBar = document.querySelector(".search-bar");
    const mobileNav = document.querySelector(".mobile-nav");
    
    let lastScrollY = window.scrollY;
    let ticking = false;
    const scrollThreshold = 100; // Min scroll before hiding

    window.addEventListener("scroll", () => {
      if (!ticking) {
        requestAnimationFrame(() => {
          const currentScrollY = window.scrollY;
          const scrollDelta = currentScrollY - lastScrollY;

          // Hide header/search when scrolling down past threshold, show when scrolling up
          if (currentScrollY > scrollThreshold && scrollDelta > 0) {
            if (topBar) topBar.classList.add("header-hidden");
            if (searchBar) searchBar.classList.add("search-hidden");
            if (mobileNav) mobileNav.classList.add("nav-hidden");
            document.body.classList.add("header-collapsed");
          } else if (scrollDelta < 0 || currentScrollY <= scrollThreshold) {
            if (topBar) topBar.classList.remove("header-hidden");
            if (searchBar) searchBar.classList.remove("search-hidden");
            if (mobileNav) mobileNav.classList.remove("nav-hidden");
            document.body.classList.remove("header-collapsed");
          }

          lastScrollY = currentScrollY;
          ticking = false;
        });
        ticking = true;
      }
    }, { passive: true });

    // Show header when user touches near top of screen
    document.addEventListener("touchstart", (e) => {
      const touchY = e.touches[0].clientY;
      if (touchY < 100) {
        if (topBar) topBar.classList.remove("header-hidden");
        if (searchBar) searchBar.classList.remove("search-hidden");
        document.body.classList.remove("header-collapsed");
      }
      const windowHeight = window.innerHeight;
      if (touchY > windowHeight - 100) {
        if (mobileNav) mobileNav.classList.remove("nav-hidden");
      }
    }, { passive: true });
  });

  // Filter panel toggle
  document.addEventListener("DOMContentLoaded", () => {
    const filterToggle = document.getElementById("filterToggle");
    const filterPanel = document.getElementById("filterPanel");
    if (!filterToggle || !filterPanel) return;

    filterToggle.addEventListener("click", () => {
      filterPanel.classList.toggle("open");
      filterToggle.classList.toggle("active");
    });

    // Close filter panel when clicking outside
    document.addEventListener("click", (e) => {
      if (!filterPanel.contains(e.target) && !filterToggle.contains(e.target)) {
        filterPanel.classList.remove("open");
        filterToggle.classList.remove("active");
      }
    });
  });
  document.addEventListener("DOMContentLoaded", () => {
    const observerOptions = {
      threshold: 0.1,
      rootMargin: "0px 0px -50px 0px"
    };

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry, index) => {
        if (entry.isIntersecting) {
          setTimeout(() => {
            entry.target.style.opacity = "1";
            entry.target.style.transform = "translateY(0)";
          }, index * 50);
          observer.unobserve(entry.target);
        }
      });
    }, observerOptions);

    document.querySelectorAll(".metric, .panel, .timeline-week, .card").forEach((el, i) => {
      el.style.opacity = "0";
      el.style.transform = "translateY(20px)";
      el.style.transition = "opacity 0.5s cubic-bezier(0.16, 1, 0.3, 1), transform 0.5s cubic-bezier(0.16, 1, 0.3, 1)";
      observer.observe(el);
    });
  });

  // PWA Service Worker Registration
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker
        .register('/news/sw.js')
        .then((registration) => {
          console.log('SW registered:', registration.scope);
        })
        .catch((error) => {
          console.log('SW registration failed:', error);
        });
    });
  }

  // PWA Install Prompt
  let deferredPrompt;
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    window.deferredInstallPrompt = deferredPrompt;
  });

  // Handle app installed event
  window.addEventListener('appinstalled', () => {
    console.log('PWA was installed');
    deferredPrompt = null;
    window.deferredInstallPrompt = null;
  });
})();
