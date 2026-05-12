/**
 * 法务实习生个人网站 · 交互脚本
 */

document.addEventListener('DOMContentLoaded', () => {

    // ========== 1. 移动端菜单切换 ==========
    const toggleBtn = document.getElementById('mobileToggle');
    const navLinks = document.querySelector('.nav-links');

    if (toggleBtn && navLinks) {
        toggleBtn.addEventListener('click', () => {
            toggleBtn.classList.toggle('active');
            navLinks.classList.toggle('active');
        });

        // 点击导航链接后关闭菜单
        navLinks.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', () => {
                toggleBtn.classList.remove('active');
                navLinks.classList.remove('active');
            });
        });
    }

    // ========== 2. 导航栏滚动阴影 ==========
    const navbar = document.querySelector('.navbar');
    let lastScrollY = 0;

    window.addEventListener('scroll', () => {
        const scrollY = window.scrollY;
        if (scrollY > 20) {
            navbar.classList.add('scrolled');
        } else {
            navbar.classList.remove('scrolled');
        }
        lastScrollY = scrollY;
    });

    // ========== 3. 技能条动画（滚动触发） ==========
    const skillBars = document.querySelectorAll('.skill-progress');

    const animateSkillBars = () => {
        skillBars.forEach(bar => {
            const rect = bar.getBoundingClientRect();
            const isVisible = rect.top < window.innerHeight - 50;
            if (isVisible) {
                const width = bar.style.width;
                bar.style.width = '0%';
                setTimeout(() => {
                    bar.style.width = width;
                }, 200);
            }
        });
    };

    // 使用 Intersection Observer 更高效地触发
    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const bar = entry.target;
                    const width = bar.style.width;
                    bar.style.width = '0%';
                    setTimeout(() => {
                        bar.style.width = width;
                    }, 200);
                    observer.unobserve(bar);
                }
            });
        }, { threshold: 0.3 });

        skillBars.forEach(bar => observer.observe(bar));
    } else {
        // fallback
        window.addEventListener('scroll', animateSkillBars);
        animateSkillBars();
    }

    // ========== 4. 卡片入场动画（淡入向上） ==========
    const animateCards = () => {
        const cards = document.querySelectorAll(
            '.exp-card, .project-card, .skill-category, .timeline-item'
        );
        cards.forEach((card, index) => {
            const rect = card.getBoundingClientRect();
            const isVisible = rect.top < window.innerHeight - 60;
            if (isVisible) {
                card.style.opacity = '1';
                card.style.transform = 'translateY(0)';
            } else {
                // 初始状态由 CSS 控制，这里只做渐进增强
                card.style.opacity = '0';
                card.style.transform = 'translateY(24px)';
                card.style.transition = `opacity 0.5s ease ${index * 0.08}s, transform 0.5s ease ${index * 0.08}s`;
            }
        });
    };

    // 初始设置：隐藏
    document.querySelectorAll('.exp-card, .project-card, .skill-category, .timeline-item')
        .forEach(card => {
            card.style.opacity = '0';
            card.style.transform = 'translateY(24px)';
            card.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
        });

    // 使用 Intersection Observer
    if ('IntersectionObserver' in window) {
        const observer2 = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.style.opacity = '1';
                    entry.target.style.transform = 'translateY(0)';
                    observer2.unobserve(entry.target);
                }
            });
        }, { threshold: 0.1 });

        document.querySelectorAll('.exp-card, .project-card, .skill-category, .timeline-item')
            .forEach(el => observer2.observe(el));
    } else {
        window.addEventListener('scroll', animateCards);
        animateCards();
    }

    // ========== 5. 联系表单处理 ==========
    const contactForm = document.getElementById('contactForm');
    if (contactForm) {
        contactForm.addEventListener('submit', (e) => {
            e.preventDefault();

            const btn = contactForm.querySelector('button');
            const originalText = btn.innerHTML;
            btn.innerHTML = '发送中 <i class="fas fa-spinner fa-spin"></i>';
            btn.disabled = true;

            // 模拟发送（实际使用请对接后端 API）
            setTimeout(() => {
                btn.innerHTML = '已发送 <i class="fas fa-check"></i>';
                btn.style.background = '#27ae60';

                setTimeout(() => {
                    btn.innerHTML = originalText;
                    btn.style.background = '';
                    btn.disabled = false;
                    contactForm.reset();
                }, 2000);
            }, 1200);
        });
    }

    // ========== 6. 平滑滚动（兼容 Safari） ==========
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            const href = this.getAttribute('href');
            if (href === '#') return;
            const target = document.querySelector(href);
            if (target) {
                e.preventDefault();
                const offset = parseInt(getComputedStyle(document.documentElement).scrollPaddingTop) || 72;
                const top = target.getBoundingClientRect().top + window.scrollY - offset;
                window.scrollTo({ top, behavior: 'smooth' });
            }
        });
    });

    console.log('✅ 法务实习生网站已加载完成');
});
