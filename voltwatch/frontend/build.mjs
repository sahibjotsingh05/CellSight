import {build} from 'esbuild';
await build({entryPoints:['glass-runtime.jsx'],bundle:true,minify:true,format:'esm',outfile:'../static/glass-runtime.js',define:{'process.env.NODE_ENV':'"production"'},legalComments:'linked'});
