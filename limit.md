 kubernetes arkasında monitoring araçlarının çalışması ve mevcut          
  park-slot süreci ile yarışa girmesi durumunu açıklayan bir kavram vardı  
  
● Aradığınız kavram "noisy neighbor" (gürültülü komşu) problemi —
  Kubernetes/bulut/sanallaştırma dünyasında tam olarak bunu tarif eden
  standart terim.

  Tanım: Aynı fiziksel node'u (veya host'u) paylaşan birden fazla iş
  yükünden biri (ya da birkaçı), kendi CPU/RAM/disk I/O kullanımıyla diğer 
  komşu workload'ların performansını düşürür — bug/doğruluk hatası değil,
  sadece sınırlı, ortak kaynağın adaletsiz/kontrolsüz paylaşılması. "Race
  condition" gibi zamanlamaya bağlı bir hata değil; "resource contention"
  (kaynak çekişmesi) kavramının bu özel, çok tenant'lı/paylaşımlı
  senaryodaki adı.

  Bizim oturumda gördüğümüz somut örneği: kubectl describe node'da
  park-slot pod'larıyla aynı node'da Elasticsearch, Logstash, Kibana,
  Prometheus, Grafana (observability yığını) birlikte çalışıyordu. Bunların
  hepsi aynı CPU/RAM havuzunu paylaşıyordu; logstash'ın 369 kez,
  prometheus'un 59 kez restart olması, muhtemelen tam bu "noisy neighbor"
  baskısından (+ ayrı docker-compose yığınlarının RAM'i tüketmesinden)
  kaynaklanıyordu — biri diğerini "gürültüyle" boğuyordu.

  Kubernetes'in buna çözümü tam olarak bizim kullandığımız mekanizma:
  requests/limits (QoS sınıfları — Guaranteed/Burstable/BestEffort). Doğru
  requests koyarsanız, scheduler her workload'a garanti bir pay ayırır;
  limits koyarsanız, hiçbir "gürültülü komşu" diğerlerinin payına
  kontrolsüzce taşamaz. Bizim park-slot'un thread sayısını CPU_LIMIT'e
  sabitlememiz de, park-slot'un KENDİSİNİN başka workload'lara karşı
  "gürültülü komşu" olmasını engelleyen aynı prensibin bir uygulamasıydı.

