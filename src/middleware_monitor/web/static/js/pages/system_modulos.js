import { montarModulos } from '/static/js/components/modulos.js';
import { toast } from '/static/js/components/toast.js';

const modulos = montarModulos({
  lista: document.getElementById('mod-lista'),
  soAdmin: document.getElementById('mod-so-admin'),
});

modulos.carregar().catch((err) => toast.error(`Não deu para carregar: ${err.message}`));
