/* =============================================
   SMORI — Luxury Window Couture
   ============================================= */

// Preloader
window.addEventListener('load', () => {
    setTimeout(() => {
        const preloader = document.getElementById('preloader');
        if (preloader) preloader.classList.add('hidden');
    }, 2200);
});

// Navbar scroll effect
const navbar = document.getElementById('navbar');
if (navbar) {
    window.addEventListener('scroll', () => {
        navbar.classList.toggle('scrolled', window.scrollY > 60);
    });
}

// Mobile menu
const navToggle = document.getElementById('navToggle');
const navLinks = document.getElementById('navLinks');
if (navToggle && navLinks) {
    navToggle.addEventListener('click', () => {
        navLinks.classList.toggle('active');
    });
    navLinks.querySelectorAll('a').forEach(link => {
        link.addEventListener('click', () => {
            navLinks.classList.remove('active');
        });
    });
}

// Product filter tabs
const tabBtns = document.querySelectorAll('.tab-btn');
const productCards = document.querySelectorAll('.product-card');

tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        tabBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const filter = btn.dataset.filter;
        productCards.forEach((card, index) => {
            const match = filter === 'all' || card.dataset.category === filter;
            if (match) {
                card.style.display = '';
                card.style.opacity = '0';
                card.style.transform = 'translateY(20px)';
                setTimeout(() => {
                    card.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
                    card.style.opacity = '1';
                    card.style.transform = 'translateY(0)';
                }, index * 80);
            } else {
                card.style.display = 'none';
            }
        });
    });
});

// Animated counter for stats
function animateCounter(el) {
    const target = parseInt(el.dataset.target);
    if (!target) return;
    const duration = 2000;
    const start = performance.now();

    function update(now) {
        const elapsed = now - start;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        const current = Math.floor(eased * target);
        el.textContent = current.toLocaleString();
        if (progress < 1) {
            requestAnimationFrame(update);
        } else {
            el.textContent = target.toLocaleString();
        }
    }
    requestAnimationFrame(update);
}

// Scroll reveal animation
const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            entry.target.classList.add('visible');
            if (entry.target.closest('.stats-bar')) {
                entry.target.querySelectorAll('.stat-number').forEach(el => {
                    if (!el.dataset.animated) {
                        el.dataset.animated = 'true';
                        animateCounter(el);
                    }
                });
            }
        }
    });
}, {
    threshold: 0.15,
    rootMargin: '0px 0px -40px 0px'
});

// Apply reveal class to elements
const revealSelectors = [
    '.product-card', '.craft-step', '.gallery-card',
    '.testimonial-card', '.contact-row', '.stats-grid',
    '.brand-inner', '.section-header', '.craft-visual',
    '.craft-content', '.contact-left', '.contact-right'
];

document.querySelectorAll(revealSelectors.join(',')).forEach((el, i) => {
    el.classList.add('reveal');
    el.style.transitionDelay = `${(i % 4) * 0.1}s`;
    revealObserver.observe(el);
});

// Form submission
const contactForm = document.getElementById('contactForm');
if (contactForm) {
    contactForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const btn = e.target.querySelector('button[type="submit"]');
        const originalText = btn.textContent;
        btn.textContent = 'Sending...';
        btn.disabled = true;
        setTimeout(() => {
            btn.textContent = 'Thank You!';
            btn.style.background = '#2d5016';
            setTimeout(() => {
                btn.textContent = originalText;
                btn.style.background = '';
                btn.disabled = false;
                e.target.reset();
            }, 2500);
        }, 1200);
    });
}

// Smooth anchor scrolling with offset
document.querySelectorAll('a[href^="#"], a[href^="/#"]').forEach(anchor => {
    anchor.addEventListener('click', function(e) {
        const href = this.getAttribute('href');
        const hash = href.startsWith('/#') ? href.slice(1) : href;
        if (href.startsWith('/#') && window.location.pathname !== '/') return; // let the browser navigate home
        if (hash === '#') return;
        e.preventDefault();
        const target = document.querySelector(hash);
        if (target) {
            const offset = 80;
            const y = target.getBoundingClientRect().top + window.scrollY - offset;
            window.scrollTo({ top: y, behavior: 'smooth' });
        }
    });
});
