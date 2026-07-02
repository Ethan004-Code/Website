AOS.init({
  duration: 900,
  easing: 'ease-out-quart',
  once: true,
  offset: 80
});

window.addEventListener('load', () => {
  const tl = gsap.timeline();
  tl.from('.navbar', { y: -40, opacity: 0, duration: 0.6, ease: 'power2.out' })
    .from('.hero-title', { y: 40, opacity: 0, duration: 0.9, ease: 'power3.out' }, '-=0.2')
    .from('.hero-sub', { y: 30, opacity: 0, duration: 0.8, ease: 'power3.out' }, '-=0.4')
    .from('.btn-primary', { y: 20, opacity: 0, duration: 0.6, ease: 'power3.out' }, '-=0.5')
    .from('.scroll-hint', { opacity: 0, duration: 0.6 }, '-=0.4');
});

document.getElementById('year').textContent = new Date().getFullYear();
