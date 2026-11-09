// Initialize AOS (Animate On Scroll)
AOS.init({
  duration: 900,
  easing: 'ease-out-quart',
  once: true,
  offset: 80
});

// GSAP animations for homepage
if (typeof gsap !== 'undefined') {
  window.addEventListener('load', () => {
    const tl = gsap.timeline();
    tl.from('.navbar', { y: -40, opacity: 0, duration: 0.6, ease: 'power2.out' })
      .from('.identity-title', { y: 40, opacity: 0, duration: 0.9, ease: 'power3.out' }, '-=0.2')
      .from('.identity-sub', { y: 30, opacity: 0, duration: 0.8, ease: 'power3.out' }, '-=0.4')
      .from('.btn-primary', { y: 20, opacity: 0, duration: 0.6, ease: 'power3.out' }, '-=0.5')
      .from('.scroll-hint', { opacity: 0, duration: 0.6 }, '-=0.4');
  });
}

// Set year in footer
const yearElement = document.getElementById('year');
if (yearElement) {
  yearElement.textContent = new Date().getFullYear();
}

// Lightbox for experience images
(function() {
  function createLightbox() {
    const overlay = document.createElement('div');
    overlay.className = 'lightbox-overlay';

    const inner = document.createElement('div');
    inner.className = 'lightbox-inner';

    const img = document.createElement('img');
    img.alt = '';

    inner.appendChild(img);
    overlay.appendChild(inner);

    const closeBtn = document.createElement('button');
    closeBtn.className = 'lightbox-close';
    closeBtn.textContent = 'Close';

    document.body.appendChild(overlay);
    document.body.appendChild(closeBtn);

    function open(src, alt) {
      img.src = src;
      img.alt = alt || '';
      overlay.classList.add('open');
      closeBtn.style.display = 'block';
      document.body.style.overflow = 'hidden';
    }

    function close() {
      overlay.classList.remove('open');
      closeBtn.style.display = 'none';
      document.body.style.overflow = '';
      img.src = '';
    }

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) close();
    });

    closeBtn.addEventListener('click', close);

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') close();
    });

    return { open, close };
  }

  const lb = createLightbox();

  // Attach to images inside .experience-images
  document.addEventListener('DOMContentLoaded', () => {
    const imgs = document.querySelectorAll('.experience-images img');
    imgs.forEach(img => {
      img.style.willChange = 'transform';
      img.style.cursor = 'pointer';
      img.addEventListener('click', () => lb.open(img.src, img.alt));
    });
  });
})();
