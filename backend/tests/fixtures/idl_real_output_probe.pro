pro idl_real_output_probe
  compile_opt idl2
  output_dir = getenv('IDLRAG_OUTPUT_DIR')
  if output_dir eq '' then message, 'IDLRAG_OUTPUT_DIR is missing'
  image = bytarr(2, 2)
  image[0, 0] = 1B
  image[1, 0] = 1B
  write_tiff, output_dir + '\\idl_probe.tif', image
  print, 'IDL real output probe completed'
end
