/* InterviewTTS Docs — Shared JS */
(function(){
  /* Progressive enhancement flag: CSS gates reveal animations on html.js */
  document.documentElement.classList.add('js');

  /* Active nav link */
  var path = location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('.nav-links a').forEach(function(a){
    var href = a.getAttribute('href');
    if(href === path){ a.classList.add('active'); }
  });

  /* Scroll reveal */
  var reveals = document.querySelectorAll('.reveal');
  if(reveals.length){
    var observer = new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if(e.isIntersecting){
          e.target.classList.add('visible');
          observer.unobserve(e.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });
    reveals.forEach(function(el){ observer.observe(el); });
  }
})();
